// Search Typeahead — frontend behaviour.
//
// Responsibilities:
//   * fetch live suggestions while typing (debounced so we don't hit the API on
//     every keystroke),
//   * full keyboard support (Up/Down/Enter/Escape) over the dropdown,
//   * submit a search to POST /search and show the dummy response,
//   * load + refresh the trending list.
//
// Plain DOM APIs, no framework — so the logic is easy to follow line by line.

const DEBOUNCE_MS = 150;     // wait this long after the last keystroke before calling /suggest
const SUGGEST_LIMIT = 10;

// --- Element handles ---
const input        = document.getElementById("search-input");
const button       = document.getElementById("search-button");
const dropdown     = document.getElementById("suggestions");
const statusLine   = document.getElementById("status");
const resultPanel  = document.getElementById("result");
const trendingList = document.getElementById("trending-list");

// --- State ---
let suggestions = [];     // current suggestion objects [{query, count}, ...]
let activeIndex = -1;     // index of the keyboard-highlighted row (-1 = none)
let debounceTimer = null;

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function setStatus(text, isError = false, loading = false) {
  statusLine.classList.toggle("error", isError);
  statusLine.innerHTML = loading ? `<span class="spinner"></span>${text}` : text;
}

// Bold the part of `text` that matches the typed `prefix` (case-insensitive).
// Returns a safe HTML string (we escape the text first to avoid injection).
function highlightPrefix(text, prefix) {
  const escaped = text.replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  if (prefix && escaped.toLowerCase().startsWith(prefix.toLowerCase())) {
    return `<span class="match">${escaped.slice(0, prefix.length)}</span>${escaped.slice(prefix.length)}`;
  }
  return escaped;
}

function formatCount(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

// ---------------------------------------------------------------------------
// Suggestions
// ---------------------------------------------------------------------------

// Debounced: schedule a fetch and cancel any pending one. This is how we "avoid
// unnecessary backend calls" — only the last keystroke in a burst hits the API.
function onInput() {
  clearTimeout(debounceTimer);
  const prefix = input.value.trim();
  if (!prefix) {
    closeDropdown();
    setStatus("");
    return;
  }
  debounceTimer = setTimeout(() => fetchSuggestions(prefix), DEBOUNCE_MS);
}

async function fetchSuggestions(prefix) {
  setStatus("Loading suggestions…", false, true);
  try {
    const res = await fetch(`/suggest?q=${encodeURIComponent(prefix)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    suggestions = data.suggestions || [];
    renderSuggestions(prefix);
    setStatus(suggestions.length ? "" : "No suggestions found.");
  } catch (err) {
    closeDropdown();
    setStatus(`Could not load suggestions (${err.message}).`, true);
  }
}

function renderSuggestions(prefix) {
  activeIndex = -1;
  if (suggestions.length === 0) {
    closeDropdown();
    return;
  }
  dropdown.innerHTML = suggestions
    .map(
      (s, i) => `
      <li class="suggestion" role="option" data-index="${i}">
        <span class="text">${highlightPrefix(s.query, prefix)}</span>
        <span class="count">${formatCount(s.count)}</span>
      </li>`
    )
    .join("");
  openDropdown();
}

function openDropdown() {
  dropdown.hidden = false;
  input.setAttribute("aria-expanded", "true");
}

function closeDropdown() {
  dropdown.hidden = true;
  dropdown.innerHTML = "";
  input.setAttribute("aria-expanded", "false");
  activeIndex = -1;
}

// Move the highlight up/down; wraps around the ends.
function moveActive(delta) {
  if (dropdown.hidden || suggestions.length === 0) return;
  activeIndex = (activeIndex + delta + suggestions.length) % suggestions.length;
  [...dropdown.children].forEach((li, i) =>
    li.classList.toggle("active", i === activeIndex)
  );
  dropdown.children[activeIndex]?.scrollIntoView({ block: "nearest" });
}

// ---------------------------------------------------------------------------
// Search submission
// ---------------------------------------------------------------------------

// Submit either the highlighted suggestion or whatever is typed.
function submitSearch() {
  const query =
    activeIndex >= 0 ? suggestions[activeIndex].query : input.value.trim();
  if (!query) return;
  input.value = query;
  closeDropdown();
  sendSearch(query);
}

async function sendSearch(query) {
  setStatus("Searching…", false, true);
  resultPanel.hidden = true;
  try {
    const res = await fetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    // Show the dummy search response.
    resultPanel.hidden = false;
    resultPanel.innerHTML = `
      <div class="result-query">You searched: “${data.query}”</div>
      <div class="result-message">Server response: ${data.message}</div>`;
    setStatus("");
    loadTrending(); // the search may have changed what's trending
  } catch (err) {
    setStatus(`Search failed (${err.message}).`, true);
  }
}

// ---------------------------------------------------------------------------
// Trending
// ---------------------------------------------------------------------------

async function loadTrending() {
  try {
    const res = await fetch("/trending?limit=10");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    trendingList.innerHTML = (data.trending || [])
      .map(
        (t, i) => `
        <button class="trending-chip" data-query="${t.query.replace(/"/g, "&quot;")}">
          <span class="rank">${i + 1}</span>${t.query}
        </button>`
      )
      .join("");
  } catch {
    trendingList.innerHTML = `<span class="status error">Could not load trending.</span>`;
  }
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------

input.addEventListener("input", onInput);

input.addEventListener("keydown", (e) => {
  switch (e.key) {
    case "ArrowDown": e.preventDefault(); moveActive(1); break;
    case "ArrowUp":   e.preventDefault(); moveActive(-1); break;
    case "Enter":     e.preventDefault(); submitSearch(); break;
    case "Escape":    closeDropdown(); break;
  }
});

button.addEventListener("click", submitSearch);

// Click a suggestion row -> fill it in and search.
dropdown.addEventListener("click", (e) => {
  const li = e.target.closest(".suggestion");
  if (!li) return;
  activeIndex = Number(li.dataset.index);
  submitSearch();
});

// Click a trending chip -> search for it.
trendingList.addEventListener("click", (e) => {
  const chip = e.target.closest(".trending-chip");
  if (!chip) return;
  input.value = chip.dataset.query;
  activeIndex = -1;
  sendSearch(chip.dataset.query);
});

// Close the dropdown when clicking outside the search area.
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search")) closeDropdown();
});

// Initial load.
loadTrending();
