"use strict";

function sortTable(table, key, direction) {
  const body = table.tBodies[0];
  const rows = Array.from(body.rows).filter((r) => !r.querySelector(".empty"));
  rows.sort((a, b) => {
    const col = Array.from(table.tHead.rows[0].cells).findIndex((c) => c.dataset.sort === key);
    const av = a.cells[col] ? a.cells[col].textContent : "";
    const bv = b.cells[col] ? b.cells[col].textContent : "";
    const an = parseFloat(av);
    const bn = parseFloat(bv);
    if (!Number.isNaN(an) && !Number.isNaN(bn)) return (an - bn) * direction;
    return av.localeCompare(bv) * direction;
  });
  rows.forEach((r) => body.appendChild(r));
}

document.querySelectorAll("table.grid th[data-sort]").forEach((th) => {
  let dir = 1;
  th.addEventListener("click", () => {
    const table = th.closest("table");
    sortTable(table, th.dataset.sort, dir);
    dir = -dir;
  });
});
