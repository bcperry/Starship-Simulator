// Starship sim viewer — server.
//
//   SOURCE=mock   -> spawns the real Python ship sim (stdio), full run control
//   SOURCE=kafka  -> consumes real topics, control produced to the control topic
//
// API:
//   GET  /api/health    liveness + feed state
//   GET  /api/state     snapshot for (re)connecting clients
//   GET  /api/stream    Server-Sent Events: snapshot flushes + heartbeats
//   POST /api/control   ship.control command (start_run / throttle / meco)

import { readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import fastifyStatic from "@fastify/static";
import Fastify from "fastify";

import type { ShipControlMessage } from "../shared/types.js";

import { FeedStore } from "./state.js";
import { makeSource, type SourceKind } from "./sources.js";

const here = path.dirname(fileURLToPath(import.meta.url));

const PORT = Number(process.env.PORT ?? 8090);
const HOST = process.env.HOST ?? "0.0.0.0";
const SOURCE = (process.env.SOURCE ?? "mock") as SourceKind;
const FLUSH_MS = Number(process.env.FLUSH_MS ?? 100);

async function main() {
  const app = Fastify({ logger: false });
  const store = new FeedStore(makeSource(SOURCE, {
    broker: process.env.BOOTSTRAP_SERVERS ?? "localhost:9092",
  }));

  const timer = setInterval(() => store.flush(), FLUSH_MS);
  timer.unref();

  app.get("/api/health", async (req, reply) => {
    void req;
    const snap = store.snapshot();
    return reply.send({
      ok: true,
      source: SOURCE,
      ts_ms: snap.ts_ms,
      engines: snap.counts.engines,
      counts: snap.counts,
      ship: {
        run_id: snap.ship?.run_id ?? null,
        meco: snap.ship?.meco ?? null,
        tank_frac: snap.ship?.tank.frac ?? null,
      },
    });
  });

  app.get("/api/state", async (req, reply) => {
    void req;
    return reply.send(store.snapshot());
  });

  app.post<{ Body: ShipControlMessage }>("/api/control", async (req, reply) => {
    if (!req.body || typeof (req.body as unknown as Record<string, unknown>).command !== "string") {
      return reply.code(400).send({ ok: false, error: "body must be a ship.control command" });
    }
    try {
      const ack = await store.sendControl(req.body);
      return reply.send(ack);
    } catch (err) {
      return reply.code(502).send({
        ok: false,
        error: `control failed: ${(err as Error).message}`,
      });
    }
  });

  app.get("/api/stream", (req, reply) => {
    reply.raw.writeHead(200, {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });
    reply.raw.write("retry: 3000\n\n");
    reply.hijack();

    const send = (payload: string) => {
      if (!reply.raw.writableEnded && !reply.raw.destroyed) {
        reply.raw.write(`data: ${payload}\n\n`);
      }
    };

    // Prime with current state, then stream flushes.
    send(store.snapshotJSON());
    const unsubscribe = store.subscribe({ send });

    const heartbeat = setInterval(() => {
      send(JSON.stringify({ heartbeat: true, ts_ms: Date.now() }));
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

  void app.listen({ port: PORT, host: HOST });
  console.log(`[viewer] source=${SOURCE} host=${HOST} port=${PORT} flush=${FLUSH_MS}ms`);

  const close = async () => {
    clearInterval(timer);
    store.flush();
    await app.close();
    process.exit(0);
  };
  process.on("SIGINT", () => void close());
  process.on("SIGTERM", () => void close());
}

void main();
