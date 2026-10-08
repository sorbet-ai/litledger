// The map as the editor holds it, kept in step with the server: loading, saving, remote changes, undo/redo.
import { MutableRefObject, useCallback, useEffect, useRef, useState } from "react";
import { useApp } from "../app/context";
import { api } from "../lib/api";
import type { MapData } from "../lib/api";
import { useOverlays } from "../ui/overlays";
import { applyOps, diff, Doc, fromServer, Op, rebase, rebaseHistory } from "./model";

type SaveStatus = "saved" | "unsaved" | "saving" | "error";
type EditReply = Awaited<ReturnType<typeof api.editMap>>;
const versionOf = (m: MapData) => m.version ?? 0;
const opKey = (op: Op) => `${String(op.op)}:${String(op.id)}`;

/** `editing`: the node whose text is being typed; remote changes wait until it is done. */
export function useMapSync(id: string, editing: string | null) {
  const { tick, lastEvent } = useApp();
  const { toast } = useOverlays();
  const [title, setTitle] = useState("");
  const [doc, setDocState] = useState<Doc | null>(null);
  const docRef = useRef<Doc | null>(null);
  /** What the server holds, as far as its replies have confirmed; `version` is that state's map version. */
  const synced = useRef<Doc | null>(null);
  const version = useRef(0);
  const past = useRef<Doc[]>([]);
  const future = useRef<Doc[]>([]);
  const [status, setStatus] = useState<SaveStatus>("saved");
  const timer = useRef<number | undefined>(undefined);

  // Everything that talks to the server runs one at a time on `chain`, so saves never overlap or land out of order,
  // and `synced` only moves forward from a server reply. Remote changes (events, a 10 s poll, a conflict reply) are
  // rebased under the local pending edits.
  const chain = useRef<Promise<void>>(Promise.resolve());
  const saveQueued = useRef(false);
  const inFlight = useRef(false);
  const failed = useRef(false);
  const failToastShown = useRef(false);
  const needResync = useRef(false);
  const backoff = useRef(0);
  const retryTimer = useRef<number | undefined>(undefined);
  const remoteVersion = useRef(0);
  const mounted = useRef(true);

  const enqueue = <T,>(job: () => Promise<T>): Promise<T> => {
    const run = chain.current.then(job);
    chain.current = run.then(() => undefined, () => undefined);
    return run;
  };

  const refreshStatus = () => {
    if (inFlight.current) return setStatus("saving");
    if (failed.current) return setStatus("error");
    const cur = docRef.current, prev = synced.current;
    setStatus(cur && prev && cur !== prev && diff(prev, cur, { keepWaiting: true }).ops.length ? "unsaved" : "saved");
  };

  const scheduleSave = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { void S.current.flush(); }, 400);
    if (!inFlight.current && !failed.current) setStatus("unsaved");
  }, []);

  const editMap = (ops: Op[], extra: { title?: string } = {}): Promise<EditReply> =>
    api.editMap({ map: id, ops, ...extra, base_version: version.current });

  /** Take a server state: it becomes `synced`, and the local doc becomes it plus the local pending changes. */
  const adopt = (m: MapData, skip?: Set<string>) => {
    const server = fromServer(m);
    const base = synced.current, local = docRef.current;
    setTitle(m.title);
    version.current = versionOf(m);
    remoteVersion.current = Math.max(remoteVersion.current, version.current);
    if (!base || !local) {
      synced.current = docRef.current = server;
      past.current = [];
      future.current = [];
      setDocState(server);
      refreshStatus();
      return;
    }
    const r = rebase(base, server, local, skip ? (op) => skip.has(opKey(op)) : undefined);
    const hadHistory = past.current.length + future.current.length > 0;
    const p = rebaseHistory(base, server, past.current), f = rebaseHistory(base, server, future.current);
    if (p && f) { past.current = p; future.current = f; }
    else {
      past.current = [];
      future.current = [];
      if (hadHistory) toast("The map was changed elsewhere, so undo history was cleared.");
    }
    synced.current = server;
    docRef.current = r.doc;
    setDocState(r.doc);
    if (r.dropped.length) toast(`${r.dropped.length} change${r.dropped.length > 1 ? "s" : ""} could not be kept: what they changed was removed or moved elsewhere.`, true);
    if (r.rescued.length) toast(`${r.rescued.length} item${r.rescued.length > 1 ? "s" : ""} added elsewhere under something you deleted ${r.rescued.length > 1 ? "were" : "was"} kept as top-level.`);
    refreshStatus();
    if (diff(server, r.doc).ops.length) scheduleSave();
  };

  const resync = async (skip?: Set<string>) => adopt(await api.map(id), skip);

  const fail = (e: unknown) => {
    failed.current = true;
    setStatus("error");
    if (!failToastShown.current) { failToastShown.current = true; toast(`Couldn't save the map: ${String(e)}`, true); }
    window.clearTimeout(retryTimer.current);
    if (mounted.current) retryTimer.current = window.setTimeout(() => { void S.current.flush(); }, Math.min(30000, 2000 * 2 ** backoff.current++));
  };

  /** Send the pending changes once (call only from inside the chain). */
  const saveOnce = async () => {
    saveQueued.current = false;
    if (needResync.current) {
      // The last request may or may not have reached the server: look before sending anything again.
      try { await resync(); needResync.current = false; } catch (e) { fail(e); return; }
    }
    const prev = synced.current, cur = docRef.current;
    if (!prev || !cur) return;
    const { ops, sent } = diff(prev, cur);
    if (!ops.length) { failed.current = false; refreshStatus(); return; }
    inFlight.current = true;
    setStatus("saving");
    let res: EditReply;
    try {
      res = await editMap(ops);
    } catch (e) {
      inFlight.current = false;
      needResync.current = true;
      fail(e);
      return;
    }
    inFlight.current = false;
    failed.current = false;
    failToastShown.current = false;
    backoff.current = 0;
    window.clearTimeout(retryTimer.current);
    const results = res.results ?? ops.map((_, i) => ({ i, ok: !res.problems.length }));
    const ok = ops.filter((_, i) => results[i]?.ok);
    const bad = ops.filter((_, i) => !results[i]?.ok);
    synced.current = applyOps(prev, ok, sent).doc;
    const expected = version.current + (ok.length ? 1 : 0);
    const stale = !!res.conflict || (typeof res.version === "number" && res.version !== expected);
    if (typeof res.version === "number" && !stale) version.current = res.version;
    if (bad.length) toast(`Couldn't save ${bad.length} change${bad.length > 1 ? "s" : ""}, so ${bad.length > 1 ? "they were" : "it was"} undone:\n${res.problems.join("\n")}`, true);
    if (bad.length || stale) {
      try { await resync(bad.length ? new Set(bad.map(opKey)) : undefined); } catch (e) { needResync.current = true; fail(e); return; }
    }
    refreshStatus();
  };

  const flush = (): Promise<void> => {
    window.clearTimeout(timer.current);
    if (saveQueued.current) return chain.current;
    saveQueued.current = true;
    return enqueue(saveOnce);
  };

  /** Fetch and rebase if the server has moved past what we hold (after any save in progress). */
  const catchUp = (poll?: boolean) => enqueue(async () => {
    // Not while a node's text is being typed (its editor would vanish if the node went); done when typing ends.
    if (!synced.current || editingRef.current) return;
    if (poll) {
      const listed = (await api.maps()).find((x) => x.id === id);
      if (typeof listed?.version === "number" && listed.version <= version.current) return;
      const m = await api.map(id);
      if (versionOf(m) > version.current) adopt(m);
    } else if (remoteVersion.current > version.current) await resync();
  });
  const editingRef = useRef<string | null>(null);
  editingRef.current = editing;
  useEffect(() => { if (!editing) S.current.catchUp().catch(() => undefined); }, [editing]);

  const S = useRef({ flush, catchUp });
  S.current = { flush, catchUp };

  const setDoc = useCallback((next: Doc, history = true) => {
    const cur = docRef.current;
    if (!cur || next === cur) return;
    if (history) { past.current = [...past.current.slice(-199), cur]; future.current = []; }
    docRef.current = next;
    setDocState(next);
    scheduleSave();
  }, [scheduleSave]);

  useEffect(() => {
    mounted.current = true;
    enqueue(async () => adopt(await api.map(id))).catch((e) => toast(String(e), true));
    const poll = window.setInterval(() => {
      if (document.visibilityState === "visible") S.current.catchUp(true).catch(() => undefined);
    }, 10000);
    const leaving = (e: BeforeUnloadEvent) => {
      const cur = docRef.current, prev = synced.current;
      if (inFlight.current || failed.current || (cur && prev && diff(prev, cur).ops.length)) { e.preventDefault(); e.returnValue = ""; }
    };
    window.addEventListener("beforeunload", leaving);
    return () => {
      mounted.current = false;
      window.clearInterval(poll);
      window.clearTimeout(retryTimer.current);
      window.removeEventListener("beforeunload", leaving);
      void S.current.flush();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  useEffect(() => {
    if (lastEvent?.op !== "map.edit") return;
    const p = lastEvent.payload as { map?: string; version?: number };
    if (p.map !== id) return;
    const v = typeof p.version === "number" ? p.version : version.current + 1;
    if (v <= version.current) return;
    remoteVersion.current = Math.max(remoteVersion.current, v);
    S.current.catchUp().catch(() => undefined);
  }, [tick, lastEvent, id]);

  const step = (from: MutableRefObject<Doc[]>, to: MutableRefObject<Doc[]>) => {
    const next = from.current.pop();
    if (!next || !docRef.current) return;
    to.current.push(docRef.current);
    docRef.current = next;
    setDocState(next);
    scheduleSave();
  };

  return {
    doc, docRef, title, status, setDoc, flush,
    undo: () => step(past, future),
    redo: () => step(future, past),
    canUndo: () => past.current.length > 0,
    canRedo: () => future.current.length > 0,
    /** Go back one step and forget it (an empty new idea leaves no undo step behind). */
    discardStep: () => {
      const before = past.current.pop();
      if (before) { docRef.current = before; setDocState(before); scheduleSave(); }
    },
    /** Save what is pending, send `ops` (expand, promote…) and bring in what they changed, keeping edits made meanwhile. */
    sendNow: (ops: Op[], onReply: (res: EditReply) => void) => {
      window.clearTimeout(timer.current);
      return enqueue(async () => {
        await saveOnce();
        onReply(await editMap(ops));
        await resync();
      });
    },
    rename: (t: string) => enqueue(async () => {
      const res = await editMap([], { title: t });
      setTitle(t);
      if (!res.conflict && res.version === version.current + 1) version.current = res.version;
      else if (res.version !== version.current) await resync();
    }),
    /** After a failed save: try again now. */
    retry: () => { backoff.current = 0; void flush(); },
  };
}
