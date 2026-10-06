import { Container, getContainer } from "@cloudflare/containers";
import { env } from "cloudflare:workers";

const CONTAINER_ID = "main";

export class ListnerContainer extends Container {
  defaultPort = 8080;
  requiredPorts = [8080];
  sleepAfter = "10m";

  envVars = {
    TELEGRAM_API_ID: env.TELEGRAM_API_ID,
    TELEGRAM_API_HASH: env.TELEGRAM_API_HASH,
    TELEGRAM_SESSION_STRING: env.TELEGRAM_SESSION_STRING,
    DATABASE_URL: env.DATABASE_URL,
    BOT_TOKEN: env.BOT_TOKEN,
    BOT_CHAT_ID: env.BOT_CHAT_ID,
    CONTROL_SECRET: env.CONTROL_SECRET,
    POLL_SECONDS: env.POLL_SECONDS,
    LEASE_SECONDS: env.LEASE_SECONDS,
    PORT: env.PORT,
  };

  override onStart() {
    console.log("Listner container started");
  }

  override onStop() {
    console.log("Listner container stopped");
  }

  override onError(error: unknown) {
    console.error("Listner container error", error);
    throw error;
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/health" && request.method === "GET") {
      return new Response(JSON.stringify({
        service: "listner",
        runtime: "cloudflare-container",
        container: CONTAINER_ID,
        ok: true,
      }), {
        headers: { "content-type": "application/json" },
      });
    }

    const container = getContainer(env.LISTNER_CONTAINER, CONTAINER_ID);
    return container.fetch(request);
  },

  async scheduled(_event: ScheduledEvent, env: Env): Promise<void> {
    const container = getContainer(env.LISTNER_CONTAINER, CONTAINER_ID);

    // This request intentionally keeps the single monitoring container active.
    // The Python supervisor owns the Telegram polling loops.
    const response = await container.fetch(
      new Request("http://container/health", { method: "GET" }),
    );
    await response.arrayBuffer();
  },
} satisfies ExportedHandler<Env>;

interface Env {
  LISTNER_CONTAINER: DurableObjectNamespace<ListnerContainer>;
  TELEGRAM_API_ID: string;
  TELEGRAM_API_HASH: string;
  TELEGRAM_SESSION_STRING: string;
  DATABASE_URL: string;
  BOT_TOKEN: string;
  BOT_CHAT_ID: string;
  CONTROL_SECRET: string;
  POLL_SECONDS: string;
  LEASE_SECONDS: string;
  PORT: string;
}
