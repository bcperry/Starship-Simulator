// Starship sim viewer — server.
//
//   SOURCE=mock  npm start          # in-UX mode: synthetic 33-engine fleet
//   SOURCE=kafka npm start          # real mode: consume the two Kafka topics
//
// API:
//   GET /api/health   liveness + feed state
//   GET /api/state    snapshot for (re)connecting clients
//   GET /api/stream   Server-Sent Events: event batches + heartbeats
//
// Serves the built web app from ../dist when present (production), otherwise
// expects the Vite dev server (with its /api proxy) in front.

import { readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import fastifyStatic from "@fastify/static";
import Fastify from "fastify";

import type { FeedEvent } from "../shared/types";

import { FeedStore } from "./state.js";
import { KafkaSource, MockSource, type Source } from "./sources.js";

const here = path.dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.PORT ?? 8080);
const HOST = process.env.HOST ?? "0.0.0.0";
const SOURCE = (process.env.SOURCE ?? "kafka") as "kafka" | "mock";
const BOOTSTRAP =
  process.env.BOOTSTRAP_SERVERS ?? process.env.BOOTSTRAP ?? "localhost:9092";
const COMMAND_TOPIC = process.env.COMMAND_TOPIC ?? "starship.engine.command";
const TELEMETRY_TOPIC =
  process.env.TELEMETRY_TOPIC ?? "starship.engine.telemetry";
const FLUSH_MS = Number(process.env.FLUSH_MS ?? 100);
const TAIL = Number(process.env.TAIL ?? 400);

async function main() {
  const app = Fastify({ logger: false });
  const store = new FeedStore();
  const sink = (ev: FeedEvent) => store.ingest(ev);

  const source: Source =
    SOURCE === "mock"
      ? new MockSource(sink)
      : new KafkaSource(
          {
            bootstrapServers: BOOTSTRAP,
            commandTopic: COMMAND_TOPIC,
            telemetryTopic: TELEMETRY_TOPIC,
          },
          sink,
          (line) => console.log(`[viewer:kafka] ${line}`),
        );

  app.get("/api/health", async () => ({
    ok: true,
    source: source.name,
    engine_count: Math.max(store.latestTelemetry.size, store.latestCommand.size),
    counts: { ...store.counts },
    ts_ms: Date.now(),
  }));

  app.get("/api/state", async () => ({
    source: source.name,
    snapshot: store.snapshot(),
    tail: store.pending.slice(-TAIL),
  }));

  app.get("/api/stream", (req, reply) => {
    reply.raw.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });
    reply.raw.write("retry: 3000\n\n");
    reply.hijack();

    const send = (payload: unknown) => {
      if (!reply.raw.writableEnded && !reply.raw.destroyed) {
        reply.raw.write(`data: ${JSON.stringify(payload)}\n\n`);
      }
    };

    // Prime reconnecting clients with the latest known state + recent tail.
    send({
      type: "hello",
      source: source.name,
      snapshot: store.snapshot(),
      tail: store.pending.slice(-TAIL),
    });

    let lastEvent = performance.now();
    const unsubscribe = store.subscribe((batch) => {
      lastEvent = performance.now();
      send({ type: "events", events: batch });
    });

    const heartbeat = setInterval(() => {
      if (performance.now() - lastEvent < 15_000) return;
      send({ type: "heartbeat", ts_ms: Date.now() });
    }, 10_000);

    const cleanup = () => {
      unsubscribe();
      clearInterval(heartbeat);
    };
    req.raw.on("close", cleanup);
    reply.raw.on("close", cleanup);
  });

  // Serve the production build when it exists.
  const dist = path.join(here, "..", "dist");
  const hasDist = await readdir(dist)
    .then((files) => files.includes("index.html"))
    .catch(() => false);

  if (hasDist) {
    await app.register(fastifyStatic, { root: dist });
    app.setNotFoundHandler((req, reply) => {
      if (req.method === "GET" && !req.url.startsWith("/api/")) {
        return reply.sendFile("index.html");
      }
      return reply.code(404).send({ error: "not found" });
    });
  }

  store.start(FLUSH_MS);
  void source.start().catch((err: Error) => {
    console.error(`[viewer] source failed to start: ${err.message}`);
  });
  console.log(
    `[viewer] source=${source.name} host=${HOST} port=${PORT} ` +
      `bootstrap=${SOURCE === "kafka" ? BOOTSTRAP : "n/a (mock)"} ` +
      `topics=[${COMMAND_TOPIC}, ${TELEMETRY_TOPIC}] flush=${FLUSH_MS}ms`,
  );

  const close = async () => {
    await app.close();
    store.stop();
    await source.stop();
    process.exit(0);
  };
  process.on("SIGINT", () => void close());
  process.on("SIGTERM", () => void close());

  try {
    await app.listen({ port: PORT, host: HOST });
  } catch (err) {
    console.error(`[viewer] failed to listen: ${(err as Error).message}`);
    process.exit(1);
  }
}

main();
