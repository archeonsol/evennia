const BLOCKS = new Set(["DIV", "P", "SECTION", "ARTICLE", "HEADER", "FOOTER", "BLOCKQUOTE", "PRE", "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL", "LI", "DL", "DT", "DD", "TABLE", "TR", "HR"]);
const HIDDEN = new Set(["SCRIPT", "STYLE", "TEMPLATE", "NOSCRIPT"]);

/** Project log HTML to text without loading resources or executing handlers. */
export function htmlToText(html: string): string {
  const template = document.createElement("template");
  template.innerHTML = html;
  let text = "";
  let boundary = false;

  const append = (value: string) => {
    if (!value) return;
    if (boundary && text && !text.endsWith("\n") && !value.startsWith("\n")) text += "\n";
    boundary = false;
    text += value;
  };
  const visit = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      append(node.textContent ?? "");
      return;
    }
    if (!(node instanceof Element)) return;
    if (HIDDEN.has(node.tagName) || node.hasAttribute("hidden")) return;
    if (node.tagName === "BR") {
      // A break after a block is an extra blank line; a trailing break inside
      // a block already supplies that block's boundary.
      if (boundary && text && !text.endsWith("\n")) text += "\n";
      boundary = false;
      text += "\n";
      return;
    }
    const block = BLOCKS.has(node.tagName);
    if (block) boundary = true;
    node.childNodes.forEach(visit);
    if (block) boundary = true;
  };
  template.content.childNodes.forEach(visit);
  return text;
}
