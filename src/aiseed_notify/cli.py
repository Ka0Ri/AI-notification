"""notify - investigate the subscribed MCP services and post one verdict to Discord."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import agent, discord, mcp_client, systemd
from .ai import summary_text
from .config import load
from .errors import NotifyError

DEFAULT_UNIT_DIR = Path.cwd() / "service" / "generated"


def _registry_path(cfg: dict) -> Path:
    """Registry path from the config, resolved against the repo, not the cwd."""
    path = Path(cfg.get("registry", "configs/services.yaml"))
    return path if path.is_absolute() else Path(cfg["path"]).parent.parent / path


def cmd_agent(args) -> int:
    cfg = load(args.config)
    services = mcp_client.load_registry(_registry_path(cfg))

    ai_cfg = {**cfg["ai"], "name": cfg["name"]}
    result, error, transcript = asyncio.run(agent.run(ai_cfg, services))

    print(f"{cfg['description']}   ({len(services)} services subscribed)")
    if transcript.unreachable:
        for name, why in transcript.unreachable.items():
            print(f"  UNREACHABLE {name}: {why}")
    print(f"  {transcript.tools_available} tools, {len(transcript.calls)} calls, {transcript.turns} turns")
    if args.show_transcript:
        for call in transcript.calls:
            print(f"    -> {call}")

    # MCP-only, by design: there is no rule-based fallback. If the agent could not
    # reach a verdict, nothing is posted and the unit fails loudly instead.
    if not result:
        print(f"\nAGENT FAILED: {error}", file=sys.stderr)
        return 2

    status = result["verdict"]
    summary = summary_text(result)
    print(f"\nVERDICT: {status}\n{summary}")
    if args.no_post:
        return (cfg["ai"].get("verdicts") or agent.DEFAULT_VERDICTS).get(status, 1)

    dcfg = cfg["notify"].get("discord")
    if not dcfg:
        raise ValueError("config has no notify.discord block")
    title = dcfg.get("title", "{name} - {status}").format(name=cfg["name"], status=status, subject="")
    footer = f"{transcript.tools_available} tools across {len(services)} services, {len(transcript.calls)} calls"
    if transcript.unreachable:
        footer += f" | unreachable: {', '.join(transcript.unreachable)}"
    discord.post(dcfg, title.rstrip(" -"), summary, footer)
    return (cfg["ai"].get("verdicts") or agent.DEFAULT_VERDICTS).get(status, 1)


def cmd_services(args) -> int:
    """Show whether each subscribed service answers, and what it offers."""
    cfg = load(args.config)
    services = mcp_client.load_registry(_registry_path(cfg))
    registry = asyncio.run(mcp_client.discover(services, timeout=args.timeout))

    width = max(len(n) for n in services)
    for name, entry in services.items():
        why = registry.unreachable.get(name)
        if why:
            print(f"  {name:<{width}}  {entry['url']}  UNREACHABLE  {why}")
            continue
        tools = [t.tool for t in registry.tools if t.service == name]
        print(f"  {name:<{width}}  {entry['url']}  ok  {len(tools)} tools: {', '.join(tools)}")

    down = len(registry.unreachable)
    print(f"\n{len(services) - down}/{len(services)} services reachable, {len(registry.tools)} tools total")
    return 1 if down else 0


def cmd_serve_node(args) -> int:
    from .mcp.node_server import serve

    serve(args.config)
    return 0


def cmd_install(args) -> int:
    cfg = load(args.config)
    units = systemd.render(cfg)
    if args.print:
        for filename, body in units.items():
            print(f"# ==== {filename} ====\n{body}")
        return 0

    outdir = Path(args.dir)
    outdir.mkdir(parents=True, exist_ok=True)
    for filename, body in units.items():
        (outdir / filename).write_text(body)
        print(f"wrote {outdir / filename}")
    timer = next(f for f in units if f.endswith(".timer"))
    print(
        f"\nTo activate:\n"
        f"  sudo cp {outdir}/notify-{cfg['name']}.* /etc/systemd/system/\n"
        f"  sudo systemctl daemon-reload\n"
        f"  sudo systemctl enable --now {timer}"
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="notify", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ag = sub.add_parser("agent", help="investigate every subscribed service and post one verdict")
    ag.add_argument("config", nargs="?", default="configs/agent.yaml", help="agent config (default: configs/agent.yaml)")
    ag.add_argument("--no-post", action="store_true", help="print the verdict instead of posting it")
    ag.add_argument("--show-transcript", action="store_true", help="list every tool call the agent made")
    ag.set_defaults(func=cmd_agent)

    svc = sub.add_parser("services", help="check which subscribed services answer, and their tools")
    svc.add_argument("config", nargs="?", default="configs/agent.yaml", help="agent config")
    svc.add_argument("--timeout", type=float, default=10.0, help="per-service connect timeout")
    svc.set_defaults(func=cmd_services)

    node = sub.add_parser("serve-node", help="run the host-level MCP server (disk, GPU, systemd)")
    node.add_argument("config", nargs="?", default="configs/node-mcp.yaml", help="node server config")
    node.set_defaults(func=cmd_serve_node)

    install = sub.add_parser("install", help="generate the systemd .service/.timer pair")
    install.add_argument("config", help="config path, or a name under $NOTIFY_CONFIG_DIR")
    install.add_argument("--dir", default=str(DEFAULT_UNIT_DIR), help="where to write the units")
    install.add_argument("--print", action="store_true", help="print the units instead of writing them")
    install.set_defaults(func=cmd_install)

    args = ap.parse_args()
    try:
        return args.func(args)
    except (NotifyError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
