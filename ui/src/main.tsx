// The page `agentlint ui` serves: it asks the server for everything, with the token the address carries. Opened
// without that token, it can ask nothing, so it says where to find the address.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Live, servedApi } from "./Live";
import "./styles.css";

const token = new URLSearchParams(window.location.search).get("token");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {token ? <Live api={servedApi(token)} /> : (
      <main className="no-token">
        <h1>agentlint</h1>
        <p>Open the address <code>agentlint ui</code> printed: it carries the token this page needs to ask for your report.</p>
      </main>
    )}
  </StrictMode>,
);
