// Persist the auth token to localStorage as soon as the frontend has it, so a
// welkom-auth login isn't re-run on every page load. Home Assistant only writes
// `hassTokens` itself when the user ticks "Keep me logged in" on the login form
// — which welkom-auth bypasses — so we mirror the token cache into storage and
// enable further writes. Polls briefly because the cache is populated
// asynchronously after page load.
(() => {
  let tries = 15;
  const timer = setInterval(() => {
    const cache = window.__tokenCache;
    if (cache && cache.tokens) {
      try {
        localStorage.setItem("hassTokens", JSON.stringify(cache.tokens));
        cache.writeEnabled = true;
      } catch (e) {
        /* storage unavailable (private mode etc.) — nothing we can do */
      }
      clearInterval(timer);
    } else if (--tries <= 0) {
      clearInterval(timer);
    }
  }, 1000);
})();
