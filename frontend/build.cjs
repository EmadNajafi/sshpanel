const path = require("node:path");
const { buildSync } = require("esbuild-wasm");

buildSync({
  entryPoints: [path.join(__dirname, "src", "overview.jsx")],
  outfile: path.join(__dirname, "..", "accounts", "static", "overview-react.js"),
  bundle: true,
  minify: true,
  legalComments: "external",
  format: "iife",
  target: "es2020",
});
