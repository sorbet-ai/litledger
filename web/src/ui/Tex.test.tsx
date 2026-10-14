// @vitest-environment jsdom
import { render, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { selectedSource, splitTex, Tex } from "./Tex";

const maths = (s: string) => splitTex(s).filter((p) => "tex" in p).map((p) => ("tex" in p ? p.tex : ""));

describe("splitTex", () => {
  it("finds inline and display maths in a paper's text", () => {
    expect(maths("The scalar gate $\\beta_{t}$ acts as a learning rate; $$S_t = S_{t-1} + v k^T$$ and \\(a\\) and \\[b\\].")).toEqual(
      ["\\beta_{t}", "S_t = S_{t-1} + v k^T", "a", "b"]);
  });
  it("leaves money and lone dollars alone", () => {
    expect(maths("It cost $5 and $10 per run.")).toEqual([]);
    expect(maths("costs $ 5 or $ 6")).toEqual([]);
    expect(maths("just one $ sign")).toEqual([]);
  });
  it("keeps escaped dollars inside a formula", () => {
    expect(maths("price $x = \\$3$ here")).toEqual(["x = \\$3"]);
  });
});

describe("Tex", () => {
  it("renders maths with KaTeX and keeps the source for selections", async () => {
    const { container } = render(<p>We set <Tex text="$\beta_{t} \in [0, 2]$ per token." /></p>);
    await waitFor(() => expect(container.querySelector(".katex")).not.toBeNull());
    const formula = container.querySelector<HTMLElement>("[data-tex]")!;
    expect(formula.dataset.tex).toBe("$\\beta_{t} \\in [0, 2]$");
    // a selection from the middle of the formula to the end of the sentence gives back the stored text
    const range = document.createRange();
    range.setStart(formula.querySelector(".katex")!.firstChild!, 0);
    range.setEnd(container.querySelector("p")!.lastChild!.lastChild!, 4);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    expect(selectedSource(sel)).toBe("$\\beta_{t} \\in [0, 2]$ per");
  });
  it("gives back the stored text from typeset lines: no added hyphens, words kept apart", () => {
    const p = document.createElement("p");
    p.innerHTML = '<span class="para typeset"><span class="tl">a multiplica<span class="hy">-</span></span>'
      + '<span class="tl">tion of <span class="tex" data-tex="$x_t$">x</span> and </span><span class="tl last">more</span></span>';
    document.body.appendChild(p);
    const range = document.createRange();
    range.selectNodeContents(p);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    expect(selectedSource(sel)).toBe("a multiplication of $x_t$ and more");
    p.remove();
  });
  it("shows text that isn't TeX as written", async () => {
    const { container } = render(<Tex text="plain words, no maths" />);
    expect(container.textContent).toBe("plain words, no maths");
  });
});
