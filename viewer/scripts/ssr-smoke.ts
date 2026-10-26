// Smoke test: render the whole app shell server-side to catch runtime
// errors (bad props, undefined access) without a browser.
import React from "react";
import { renderToString } from "react-dom/server";

import App from "../src/App";

const html = renderToString(React.createElement(App));
if (!html.includes("Starship")) throw new Error("brand missing from render");
console.log(`ssr smoke OK — ${html.length} chars`);
