/* Onto — the only hand-written JS. htmx does the requests; this file wires
 * the few things htmx can't: timezone capture and (from Phase 1) drag-drop. */

(function () {
  "use strict";

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
})();
