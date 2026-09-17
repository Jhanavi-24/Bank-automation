"""
Perception layer: turns a live Playwright page into (a) a numbered list of
interactive elements an LLM can reason about, and (b) a ranked list of
candidate locators for any given element.

This is the seam the write-up calls out between "how we perceive/act on a
surface" and "the recorded flow": everything above this module (the
discovery loop, the replay engine) only ever talks about
`ElementInfo` / `Locator` objects, never raw DOM. Swapping the surface for a
desktop app later means replacing this module's implementation (querying an
accessibility API instead of running JS) while the rest of the system is
unchanged. See REPORT.md section 4.

Deliberately does NOT rely on: element ids, data-testid, CSS classes, or any
attribute a developer would have had to add for automation's sake -- the mock
app doesn't have any, matching the brief's "legacy apps essentially never
have test IDs" reality. The one heuristic beyond ARIA role/accessible-name is
`row_label`, which reads the sibling <td> text in the same <tr> -- a stand-in
for "the only label a human operator sees is the text next to the field."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from playwright.sync_api import Page

from src.artifact.schema import Locator, LocatorStrategy

_COLLECT_JS = r"""
() => {
  function cssPath(el) {
    if (!(el instanceof Element)) return '';
    const path = [];
    while (el && el.nodeType === Node.ELEMENT_NODE && el.tagName.toLowerCase() !== 'html') {
      let selector = el.tagName.toLowerCase();
      if (el.parentElement) {
        const siblings = Array.from(el.parentElement.children).filter(s => s.tagName === el.tagName);
        if (siblings.length > 1) {
          const idx = siblings.indexOf(el) + 1;
          selector += `:nth-of-type(${idx})`;
        }
      }
      path.unshift(selector);
      el = el.parentElement;
    }
    return path.join(' > ');
  }

  function rowLabel(el) {
    const tr = el.closest('tr');
    if (!tr) return null;
    const cellWithEl = el.closest('td');
    const cells = Array.from(tr.querySelectorAll('td'));
    for (const cell of cells) {
      if (cell !== cellWithEl && cell.innerText && cell.innerText.trim()) {
        return cell.innerText.trim();
      }
    }
    return null;
  }

  function accessibleName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    if (el.id) {
      const lbl = document.querySelector(`label[for="${el.id}"]`);
      if (lbl && lbl.innerText.trim()) return lbl.innerText.trim();
    }
    const closestLabel = el.closest('label');
    if (closestLabel && closestLabel.innerText.trim()) return closestLabel.innerText.trim();
    if (el.tagName === 'INPUT' && (el.type === 'submit' || el.type === 'button')) return el.value || 'Submit';
    // A <select>'s innerText is every option concatenated -- not a usable
    // accessible name. Without a real <label>, fall through to row_label
    // (the adjacent table-cell heuristic) instead of this garbage.
    if (el.tagName === 'SELECT') return '';
    if (el.innerText && el.innerText.trim()) return el.innerText.trim().slice(0, 80);
    if (el.getAttribute('placeholder')) return el.getAttribute('placeholder');
    return '';
  }

  function roleOf(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'submit' || t === 'button') return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      return 'textbox';
    }
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'td' || tag === 'th') return 'cell';
    if (tag === 'dt' || tag === 'dd') return 'text';
    return tag;
  }

  // Interactive controls the agent can act on, plus *leaf* readable nodes
  // (headings, table cells) the agent can point to for extract_output /
  // checkpoint purposes. A td/th/heading is only "leaf" if it has no
  // interactive or table-structural descendant of its own -- otherwise
  // it's a layout container (this app nests tables for its own chrome)
  // and would just dump the whole page as one noisy blob of text.
  const interactiveSel = 'a[href], button, input, select, textarea, [role]';
  const readableSel = 'h1, h2, h3, h4, h5, h6, td, th, dt, dd';
  const interactiveNodes = Array.from(document.querySelectorAll(interactiveSel));
  const readableNodes = Array.from(document.querySelectorAll(readableSel)).filter(
    el => !el.querySelector('table, input, select, textarea, button, a, [role]')
  );
  const nodes = [...interactiveNodes, ...readableNodes];
  return nodes
    .filter(el => {
      const r = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return r.width > 0 && r.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
    })
    .map(el => {
      const r = el.getBoundingClientRect();
      return {
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute('type') || '',
        role: roleOf(el),
        name: accessibleName(el),
        row_label: rowLabel(el),
        html_name: el.getAttribute('name') || null,
        test_id: el.getAttribute('data-testid') || el.getAttribute('data-test') || null,
        text: (el.innerText || '').trim().slice(0, 120),
        css_path: cssPath(el),
        bbox: { x: r.x, y: r.y, width: r.width, height: r.height },
      };
    });
}
"""


@dataclass
class ElementInfo:
    index: int
    tag: str
    input_type: str
    role: str
    name: str
    row_label: Optional[str]
    html_name: Optional[str]
    test_id: Optional[str]
    text: str
    css_path: str
    bbox: dict
    frame_selector: Optional[str] = None  # None = main frame

    def candidate_locators(self) -> List[Locator]:
        candidates: List[Locator] = []
        if self.test_id:
            candidates.append(Locator(
                strategy=LocatorStrategy.TEST_ID, value=self.test_id, frame=self.frame_selector,
                notes="Explicit automation attribute (data-testid/data-test).",
            ))
        value_cell_with_stable_label = self.tag in ("td", "th") and self.row_label
        if self.name and not value_cell_with_stable_label:
            # Skipped for a value cell that has a stable row label: this element's
            # "accessible name" IS its own dynamic text (e.g. a balance), which would
            # make role_name a locator that only ever matches *this* run's data. The
            # row-label-based ROW_VALUE_TEXT candidate below is the reusable version
            # of the same idea, so it's listed instead, not merely after.
            candidates.append(Locator(
                strategy=LocatorStrategy.ROLE_NAME, value=f"{self.role}|{self.name}", frame=self.frame_selector,
                notes="ARIA role + accessible name; survives markup/DOM restructuring.",
            ))
        if self.row_label and self.tag in ("input", "select", "textarea"):
            candidates.append(Locator(
                strategy=LocatorStrategy.LABEL_TEXT, value=self.row_label, frame=self.frame_selector,
                notes="Adjacent table-cell label text (legacy layout has no <label for>).",
            ))
        if self.row_label and self.tag in ("td", "th"):
            # This element IS the value cell (e.g. the balance), and self.row_label is the
            # stable label cell next to it (e.g. "Savings Balance"). Recorded this way instead
            # of by the value's own text, this locator keeps working when the value changes
            # (a different member's balance) -- which is the whole point of recording a
            # *reusable* capability rather than one frozen to the discovery-time data.
            candidates.append(Locator(
                strategy=LocatorStrategy.ROW_VALUE_TEXT, value=self.row_label, frame=self.frame_selector,
                notes="Sibling table cell identified by its stable row label, not by its own (dynamic) value.",
            ))
        text_tags = ("a", "button", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd")
        if self.text and self.tag in text_tags and not value_cell_with_stable_label:
            candidates.append(Locator(
                strategy=LocatorStrategy.TEXT_EXACT, value=self.text, frame=self.frame_selector,
                notes="Exact visible text of a link/button.",
            ))
        candidates.append(Locator(
            strategy=LocatorStrategy.CSS, value=self.css_path, frame=self.frame_selector,
            notes="Structural CSS path -- breaks if layout changes; kept as a fallback only.",
        ))
        cx = round(self.bbox["x"] + self.bbox["width"] / 2)
        cy = round(self.bbox["y"] + self.bbox["height"] / 2)
        candidates.append(Locator(
            strategy=LocatorStrategy.COORDINATES, value=f"{cx},{cy}", frame=self.frame_selector,
            notes="Absolute viewport coordinates -- last resort, does not survive layout changes.",
        ))
        return candidates


def snapshot(page: Page) -> List[ElementInfo]:
    """Enumerate interactive elements across the main frame and same-origin iframes."""
    elements: List[ElementInfo] = []
    index = 0
    for frame in page.frames:
        if frame != page.main_frame:
            try:
                frame_el = frame.frame_element()
                src = frame_el.get_attribute("src") or ""
                frame_selector = f"iframe[src='{src}']" if src else None
            except Exception:
                frame_selector = None
        else:
            frame_selector = None

        try:
            raw = frame.evaluate(_COLLECT_JS)
        except Exception:
            continue  # cross-origin or torn-down frame; skip rather than crash the observe step

        for item in raw:
            elements.append(ElementInfo(
                index=index,
                tag=item["tag"],
                input_type=item["type"],
                role=item["role"],
                name=item["name"],
                row_label=item["row_label"],
                html_name=item["html_name"],
                test_id=item["test_id"],
                text=item["text"],
                css_path=item["css_path"],
                bbox=item["bbox"],
                frame_selector=frame_selector,
            ))
            index += 1
    return elements


def render_for_llm(elements: List[ElementInfo]) -> str:
    """Compact textual description of the page's interactive elements, for the LLM prompt."""
    lines = []
    for e in elements:
        label = e.name or e.row_label or e.text or "(unlabeled)"
        frame_note = f" [inside frame {e.frame_selector}]" if e.frame_selector else ""
        lines.append(f"[{e.index}] {e.role} \"{label}\" <{e.tag}{' type=' + e.input_type if e.input_type else ''}>{frame_note}")
    return "\n".join(lines) if lines else "(no interactive elements detected)"
