# TODO — command pipeline & engine-as-a-process

Status snapshot: command/telemetry **schemas and in-process pipeline exist**;
the standalone-process half + exports/tests/demos/docs are left below.

## Done (this session)
- `messages.py`: `engine.command` schema (v1) + `to_command` / `validate_command`;
  telemetry gains `command_seq` (echoes which command it answers).
- `consumers.py`: `CommandSource` protocol, `InMemoryCommandSource`,
  `KafkaCommandSource` (one consumer group per engine id).
- `engine_feeder.py`: `EngineProcess` — one physics step per command,
  mis-addressed/bad commands rejected, `run(max_steps, idle_timeout)`.

## Remaining
1. **CLI entrypoint** — `simulator/telemetry/__main__.py`:
   `python -m simulator.telemetry --engine-id sh-07 --command-topic sh07.engine.command
   --telemetry-topic starship.engine.telemetry --bootstrap-servers localhost:9092
   [--load-kg 4000] [--max-steps 2000] [--idle-timeout 120]`
   (wire `KafkaCommandSource` + `KafkaPublisher` → `EngineProcess.run()`).
2. **Exports** — `simulator/telemetry/__init__.py` and `simulator/__init__.py`
   still lack: `EngineProcess`, `CommandMessage`, `to_command`,
   `validate_command`, `InMemoryCommandSource`, `KafkaCommandSource`,
   `ENGINE_COMMAND`.
3. **Tests** (new file, e.g. `tests/test_engine_process.py`):
   - step-per-command, `command_seq` echo, dt/throttle/ambient honored from command
   - mis-addressed & malformed command rejected/skipped in `run`
   - `run()`: `max_steps`, idle-timeout returns promptly, unusable cmd skipped
   - 33-engine command-driven fleet + one engine to cutoff via `aggregate_fleet`
4. **Demo** — `scripts/command_fleet_demo.py`: vehicle side offers 33 commands/step,
   33 `EngineProcess` objects (in-mem command sources) step, drain → `aggregate_fleet`;
   one engine carries 500 kg load → show `any_cut` appear, firing 32.
5. **Docs** — main README telemetry section: command-topic pattern, per-engine
   process launch command, 33-engine example; one-liner in `simulator/engine/README.md`.
6. **Hygiene** — `KafkaPublisher.close(timeout=...)` kwarg so a dead broker can't
   hold shutdown 30 s; `ruff check`; full `pytest` green; final commit.
