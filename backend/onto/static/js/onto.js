/* Onto — the only hand-written JS. htmx does the requests; this file wires
 * the few things htmx can't: timezone capture, drag-drop, and form niceties.
 * Everything degrades: no JS still leaves the "+ Add" buttons and plain forms. */

(function () {
  "use strict";

  // ── Timezone capture ──────────────────────────────────────────────────
  // Fill the hidden timezone field on auth forms so period boundaries can be
  // computed in the user's local time. Absence is fine — the server keeps
  // whatever it already has.
  document.querySelectorAll(".js-timezone").forEach(function (el) {
    try {
      el.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch (e) {
      /* leave blank */
    }
  });

  // ── Drag a goal chip into the period ──────────────────────────────────
  // The dragged clone is only a gesture: on drop we remove it and ask the
  // server for the real card, so the server-rendered HTML stays the single
  // source of truth. Mobile Safari drag is temperamental, which is why every
  // chip also carries a first-class "+ Add" button.
  function wireDragDrop() {
    var commitList = document.getElementById("commit-list");
    var libraryList = document.getElementById("library-list");
    if (!commitList || !libraryList || typeof Sortable === "undefined") return;

    new Sortable(libraryList, {
      group: { name: "goals", pull: "clone", put: false },
      sort: false,
      // Don't let a tap on "+ Add" start a drag.
      filter: ".chip-add",
      preventOnFilter: false,
    });

    new Sortable(commitList, {
      group: { name: "goals", put: true },
      sort: false,
      onAdd: function (evt) {
        var goalId = evt.item.getAttribute("data-goal-id");
        evt.item.remove();
        if (!goalId) return;
        htmx.ajax("POST", commitList.dataset.addUrl, {
          target: "#commit-list",
          swap: "beforeend",
          values: { goal_id: goalId },
        });
      },
    });
  }
  wireDragDrop();

  // Keep the "drag something over" hint honest as cards come and go.
  var emptyHint = document.getElementById("commit-empty");
  if (emptyHint) {
    var sync = function () {
      var list = document.getElementById("commit-list");
      emptyHint.classList.toggle(
        "hidden",
        !!(list && list.querySelector(".commit-card"))
      );
    };
    document.body.addEventListener("htmx:afterSwap", sync);
    document.body.addEventListener("htmx:oobAfterSwap", sync);
    sync();
  }

  // ── Goal form niceties ────────────────────────────────────────────────
  // Show the "how many times" row only for kinds that count, and filter the
  // subcategory select to the chosen category.
  var form = document.querySelector(".goal-form");
  if (form) {
    var targetRow = form.querySelector(".js-target-row");
    var syncKind = function () {
      if (!targetRow) return;
      var kind = form.querySelector('input[name="kind"]:checked');
      targetRow.style.display = kind && kind.value === "countable" ? "" : "none";
    };
    form.querySelectorAll('input[name="kind"]').forEach(function (el) {
      el.addEventListener("change", syncKind);
    });
    syncKind();

    var cat = form.querySelector(".js-category");
    var sub = form.querySelector(".js-subcategory");
    if (cat && sub) {
      var syncSubs = function () {
        var chosen = cat.value;
        var visible = 0;
        sub.querySelectorAll("option[data-category]").forEach(function (opt) {
          var match = opt.dataset.category === chosen;
          opt.hidden = !match;
          if (match) visible++;
          if (!match && opt.selected) sub.value = "";
        });
        sub.closest("label").style.display = visible ? "" : "none";
      };
      cat.addEventListener("change", syncSubs);
      syncSubs();
    }
  }
})();
