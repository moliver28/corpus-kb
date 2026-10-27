import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";
import { loadRuntimeConfig } from "./api/runtimeConfig";
import App from "./App";
import "./ui.css";

// One boot load of /app/runtime-config.json; every consumer (the client
// wait cap now, poll intervals and upload caps later) reads the cached
// snapshot — never a second fetch pipeline.
void loadRuntimeConfig();

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        theme={{
          token: {
            colorPrimary: "#0958d9",
            // AA contrast: antd defaults (#8c8c8c on white) fail 4.5:1.
            colorTextDescription: "#595959",
            colorTextSecondary: "#595959",
          },
        }}
      >
        <App />
      </ConfigProvider>
    </QueryClientProvider>
  </StrictMode>,
);
