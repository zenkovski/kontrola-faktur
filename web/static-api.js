/* Static web version: answers the page's /api/* calls in the browser.
   Samples and the AI reads are pre-made JSON (read once by DeepSeek, cached).
   Checking runs the project's own Python code through Pyodide. No server, no AI calls. */
(() => {
  const realFetch = window.fetch.bind(window);
  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), {status, headers: {"Content-Type": "application/json"}});

  let ready = null;
  function python() {
    if (!ready) ready = (async () => {
      const py = await loadPyodide();
      await py.loadPackage(["pydantic", "Pillow", "sqlite3", "micropip"]);
      await py.pyimport("micropip").install("qrcode");
      const files = await realFetch("py/bundle.json").then(r => r.json());
      for (const [path, text] of Object.entries(files)) {
        const dir = "/app/" + path.split("/").slice(0, -1).join("/");
        py.FS.mkdirTree(dir);
        py.FS.writeFile("/app/" + path, text);
      }
      py.runPython('import sys; sys.path[:0] = ["/app", "/app/scripts", "/app/web"]; import static_api');
      return py.pyimport("static_api");
    })();
    return ready;
  }
  python(); // start loading right away, it takes a few seconds

  const call = async (fn, ...args) => {
    const api = await python();
    const out = JSON.parse(api[fn](...args));
    return [out.status, out.body];
  };

  function htmlToImage(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const xhtml = new XMLSerializer().serializeToString(doc.documentElement);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="794" height="1123" viewBox="0 0 794 1123">` +
      `<rect width="100%" height="100%" fill="#fff"/><foreignObject width="794" height="1123">${xhtml}</foreignObject></svg>`;
    return URL.createObjectURL(new Blob([svg], {type: "image/svg+xml"}));
  }

  window.fetch = async (url, opts = {}) => {
    const u = String(url);
    if (!u.startsWith("/api/")) return realFetch(url, opts);
    const path = u.split("?")[0];
    try {
      if (path === "/api/samples") return realFetch("api/samples.json");
      if (path === "/api/books") return realFetch("api/books.json");
      if (path === "/api/usage") return json({reader: "static", limit: 0, used: 0, left: 0});
      if (path.startsWith("/api/read/")) {
        const reads = await realFetch("api/reads.json").then(r => r.json());
        const r = reads[path.slice("/api/read/".length)];
        return r ? json(r) : json({detail: "Taková faktura není."}, 404);
      }
      if (path === "/api/upload")
        return json({detail: "Na webu běží jen ukázky (čtení přes AI stojí peníze). Vlastní fakturu přečte lokální verze z GitHubu."}, 422);
      if (path === "/api/reset") { (await python()).reset(); return json({ok: true}); }
      if (path === "/api/check") { const [s, b] = await call("check", opts.body); return json(b, s); }
      if (path === "/api/render") {
        const [s, b] = await call("render", opts.body);
        return s === 200 ? json({image: htmlToImage(b.html)}) : json(b, s);
      }
      return json({detail: "Not found"}, 404);
    } catch (e) {
      console.error(e);
      return json({detail: "Python v prohlížeči se nenačetl. Zkus obnovit stránku."}, 500);
    }
  };
})();
