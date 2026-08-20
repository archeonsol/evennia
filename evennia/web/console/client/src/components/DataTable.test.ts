import { render } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";

import Harness from "./DataTableHarness.svelte";

/* The requirement this component exists for: the Records lens reads
 * `SessionRecord`, which grows by one row per connection, and an operator who
 * raises the page size should get a slow query rather than a browser that
 * stops responding. */

function rows(count: number) {
  return Array.from({ length: count }, (_, index) => ({ id: index, name: `row ${index}` }));
}

describe("DataTable", () => {
  it("renders a short listing whole", () => {
    const { container } = render(Harness, { rows: rows(12) });
    expect(container.querySelectorAll("tbody tr[data-row]")).toHaveLength(12);
  });

  it("adds no spacers when nothing is virtualized", () => {
    // A spacer on a short table would add height the listing does not have.
    const { container } = render(Harness, { rows: rows(12) });
    expect(container.querySelectorAll("tr.spacer")).toHaveLength(0);
  });

  it("renders a window rather than forty thousand rows", () => {
    const { container } = render(Harness, { rows: rows(40000) });
    const rendered = container.querySelectorAll("tbody tr[data-row]").length;
    expect(rendered).toBeGreaterThan(0);
    expect(rendered).toBeLessThan(200);
  });

  it("keeps the scrollbar describing the whole listing", () => {
    // The operator must not be able to tell. A spacer below carries the height
    // of every row that is not in the DOM.
    const { container } = render(Harness, { rows: rows(40000) });
    const spacers = container.querySelectorAll("tr.spacer");
    expect(spacers.length).toBeGreaterThan(0);
    const total = Array.from(spacers).reduce(
      (sum, node) => sum + parseInt((node as HTMLElement).style.height || "0", 10),
      0,
    );
    // 40000 rows at 26px, less the handful actually rendered.
    expect(total).toBeGreaterThan(1_000_000);
  });

  it("tells assistive technology how many rows there really are", () => {
    const { container } = render(Harness, { rows: rows(40000) });
    expect(container.querySelector("table")?.getAttribute("aria-rowcount")).toBe("40000");
  });

  it("renders every column heading", () => {
    const { container } = render(Harness, { rows: rows(3) });
    const headings = Array.from(container.querySelectorAll("th")).map((node) => node.textContent);
    expect(headings).toEqual(["ID", "NAME"]);
  });

  it("survives an empty listing", () => {
    const { container } = render(Harness, { rows: [] });
    expect(container.querySelectorAll("tbody tr")).toHaveLength(0);
    expect(container.querySelectorAll("th")).toHaveLength(2);
  });
});
