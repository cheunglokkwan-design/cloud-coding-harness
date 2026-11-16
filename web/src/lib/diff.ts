// Minimal unified-diff parser for the Changes view.

export interface DiffLine {
  kind: "add" | "del" | "ctx" | "hunk";
  text: string;
  oldNo?: number;
  newNo?: number;
}

export interface DiffFile {
  path: string;
  added: number;
  removed: number;
  binary: boolean;
  lines: DiffLine[];
}

export function parseUnifiedDiff(patch: string): DiffFile[] {
  const files: DiffFile[] = [];
  let cur: DiffFile | null = null;
  let oldNo = 0;
  let newNo = 0;
  for (const raw of patch.split("\n")) {
    if (raw.startsWith("diff --git ")) {
      const m = raw.match(/^diff --git a\/(.+?) b\/(.+)$/);
      cur = { path: m ? m[2] : raw.slice(11), added: 0, removed: 0, binary: false, lines: [] };
      files.push(cur);
      continue;
    }
    if (!cur) continue;
    if (raw.startsWith("Binary files") || raw.startsWith("GIT binary patch")) {
      cur.binary = true;
      continue;
    }
    if (raw.startsWith("+++ ") || raw.startsWith("--- ") || raw.startsWith("index ") ||
        raw.startsWith("new file mode") || raw.startsWith("deleted file mode") || raw.startsWith("similarity") ||
        raw.startsWith("rename ")) {
      continue;
    }
    const h = raw.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
    if (h) {
      oldNo = Number(h[1]);
      newNo = Number(h[2]);
      cur.lines.push({ kind: "hunk", text: raw });
      continue;
    }
    if (cur.binary) continue;
    if (raw.startsWith("+")) {
      cur.added++;
      cur.lines.push({ kind: "add", text: raw.slice(1), newNo: newNo++ });
    } else if (raw.startsWith("-")) {
      cur.removed++;
      cur.lines.push({ kind: "del", text: raw.slice(1), oldNo: oldNo++ });
    } else if (raw.startsWith(" ")) {
      cur.lines.push({ kind: "ctx", text: raw.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
  }
  return files;
}

// Strip ANSI escape sequences from terminal output.
// eslint-disable-next-line no-control-regex
const ANSI = /\u001b\[[0-9;?]*[ -/]*[@-~]|\u001b\][^\u0007]*\u0007/g;
export const stripAnsi = (s: string) => s.replace(ANSI, "");
