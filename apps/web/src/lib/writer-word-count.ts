/** Approximate the prose word count of LaTeX source.
 *
 * The counter intentionally ignores the preamble, comments, citations,
 * references, labels, URLs, math and code-like environments. It keeps the
 * human-readable arguments of ordinary formatting commands, so
 * `\textbf{supported claim}` contributes two words.
 */
export function countLatexWords(source: string): number {
  let prose = stripLatexComments(source);
  const documentBody = prose.match(
    /\\begin\s*\{document\}([\s\S]*?)\\end\s*\{document\}/i,
  );
  if (documentBody) prose = documentBody[1];

  prose = prose
    .replace(
      /\\begin\s*\{(?:equation\*?|align\*?|gather\*?|multline\*?|displaymath|math|verbatim\*?|lstlisting|minted|tikzpicture|pgfpicture)\}[\s\S]*?\\end\s*\{\s*(?:equation\*?|align\*?|gather\*?|multline\*?|displaymath|math|verbatim\*?|lstlisting|minted|tikzpicture|pgfpicture)\s*\}/gi,
      " ",
    )
    .replace(/\$\$[\s\S]*?\$\$/g, " ")
    .replace(/\\\[[\s\S]*?\\\]/g, " ")
    .replace(/\\\([\s\S]*?\\\)/g, " ")
    .replace(/(?<!\\)\$[^$\n]*?(?<!\\)\$/g, " ")
    .replace(
      /\\(?:cite\w*|ref|pageref|label|url|href|includegraphics|input|include|bibliography|bibliographystyle|documentclass|usepackage|author|date)\*?(?:\s*\[[^\]]*\])*\s*\{[^{}]*\}(?:\s*\{[^{}]*\})?/gi,
      " ",
    )
    .replace(/\\begin\s*\{[^{}]*\}(?:\s*\[[^\]]*\])?/gi, " ")
    .replace(/\\end\s*\{[^{}]*\}/gi, " ")
    .replace(/\\[a-zA-Z@]+\*?(?:\s*\[[^\]]*\])*/g, " ")
    .replace(/\\./g, " ")
    .replace(/[{}[\]~^&_#]/g, " ");

  return prose.match(/[\p{L}\p{N}]+(?:['’.-][\p{L}\p{N}]+)*/gu)?.length ?? 0;
}

/** Remove unescaped LaTeX comments without treating `\%` as a comment. */
function stripLatexComments(source: string): string {
  return source
    .split(/\r?\n/)
    .map((line) => {
      for (let index = 0; index < line.length; index += 1) {
        if (line[index] !== "%") continue;
        let slashes = 0;
        for (let cursor = index - 1; cursor >= 0 && line[cursor] === "\\"; cursor -= 1) {
          slashes += 1;
        }
        if (slashes % 2 === 0) return line.slice(0, index);
      }
      return line;
    })
    .join("\n");
}
