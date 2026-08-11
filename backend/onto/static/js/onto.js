/* Onto — the only hand-written JS. htmx does the requests; this file wires
 * the few things htmx can't: timezone capture and form niceties.
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

  // Keep the "tap + Add" hint honest as cards come and go.
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
  // The "how many times" count only makes sense for the default kinds; a
  // special kind picked under More options hides it. The subcategory select
  // filters to the chosen category.
  var form = document.querySelector(".goal-form");
  if (form) {
    var targetRow = form.querySelector(".js-target-row");
    var special = form.querySelector(".js-kind");
    var syncKind = function () {
      if (!targetRow) return;
      var hide = special && special.value && special.value !== "countable";
      targetRow.style.display = hide ? "none" : "";
    };
    if (special) special.addEventListener("change", syncKind);
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
