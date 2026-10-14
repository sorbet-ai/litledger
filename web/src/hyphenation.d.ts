declare module "hyphenation.en-us" {
  const patterns: { id: string; leftmin: number; rightmin: number; patterns: Record<string, string> };
  export default patterns;
}
