import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";
import { loadRuntimeConfig } from "./api/runtimeConfig";
import App from "./App";
import { corpusKbTheme } from "./theme";

// Chrome fonts load up front: Plex Sans for the UI face, Plex Mono for
// the machine voice. The evidence serif (Source Serif 4) rides in the
// Ask stage chunk, where the only evidence text renders.
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "./ui.css";

// One boot load of /app/runtime-config.json; every consumer (the client
// wait cap now, poll intervals and upload caps later) reads the cached
// snapshot — never a second fetch pipeline.
void loadRuntimeConfig();

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider theme={corpusKbTheme}>
        <App />
      </ConfigProvider>
    </QueryClientProvider>
  </StrictMode>,
);
