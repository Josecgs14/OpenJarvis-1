"""``jarvis listen`` — always-on local wake-word voice assistant."""

from __future__ import annotations

import sys

import click
from rich.console import Console


@click.command()
@click.option("-a", "--agent", "agent_name", default=None, help="Agent type.")
@click.option("-m", "--model", "model_name", default=None, help="Model to use.")
@click.option("-e", "--engine", "engine_key", default=None, help="Engine backend.")
@click.option("--tools", default=None, help="Comma-separated tool names.")
@click.option(
    "--wake-phrase",
    "wake_phrases",
    multiple=True,
    help='Wake phrase to listen for (repeatable; default: from config, e.g. "hola claude").',
)
@click.option(
    "--speak/--no-speak",
    "speak_replies",
    default=None,
    help="Speak responses aloud via the TTS backend (default: from config).",
)
def listen(
    agent_name: str | None,
    model_name: str | None,
    engine_key: str | None,
    tools: str | None,
    wake_phrases: tuple[str, ...],
    speak_replies: bool | None,
) -> None:
    """Listen on the microphone for a wake phrase (default "Hola Claude").

    Once the wake phrase is heard, the following command is sent to the
    assistant — with full access to its tools — and the reply is printed
    (and, if a TTS backend is configured, spoken back).

    Requires a microphone and the speech extras:

        pip install "openjarvis[speech,speech-mic]"
    """
    console = Console(stderr=True)

    from openjarvis.core.config import load_config

    config = load_config()

    from openjarvis.speech._discovery import get_speech_backend, get_tts_backend

    speech_backend = get_speech_backend(config)
    if speech_backend is None:
        console.print(
            "[red]No speech-to-text backend available.[/red] "
            'Install one with: pip install "openjarvis[speech]"'
        )
        sys.exit(1)

    speak = config.voice.speak_replies if speak_replies is None else speak_replies
    tts_backend = get_tts_backend(config) if speak else None
    if speak and tts_backend is None:
        console.print(
            "[yellow]No text-to-speech backend available; replies will be "
            "text-only.[/yellow]"
        )

    phrases = list(wake_phrases) or list(config.voice.wake_phrases)

    # Resolve engine + model (mirrors `jarvis chat`)
    from openjarvis.engine import discover_engines, discover_models, get_engine
    from openjarvis.intelligence import register_builtin_models

    register_builtin_models()
    resolved = get_engine(config, engine_key)
    if resolved is None:
        console.print("[red]No inference engine available.[/red]")
        sys.exit(1)
    engine_name, engine = resolved

    model = model_name or config.intelligence.default_model
    if not model:
        all_engines = discover_engines(config)
        all_models = discover_models(all_engines)
        engine_models = all_models.get(engine_name, [])
        if engine_models:
            model = engine_models[0]
        else:
            console.print("[red]No model available.[/red]")
            sys.exit(1)

    # Resolve agent (mirrors `jarvis chat`)
    import openjarvis.agents  # noqa: F401 — trigger registration
    from openjarvis.core.events import EventBus
    from openjarvis.core.registry import AgentRegistry

    agent_key = agent_name or config.voice.agent or config.agent.default_agent
    agent = None
    if agent_key and agent_key != "none" and AgentRegistry.contains(agent_key):
        agent_cls = AgentRegistry.get(agent_key)
        kwargs: dict = {"bus": EventBus()}
        if getattr(agent_cls, "accepts_tools", False):
            from openjarvis.cli._tool_names import resolve_tool_names

            tool_names_list = resolve_tool_names(
                tools,
                getattr(config.tools, "enabled", None),
                getattr(config.agent, "tools", None),
            )
            if tool_names_list:
                import openjarvis.tools  # noqa: F401 — trigger registration
                from openjarvis.core.registry import ToolRegistry
                from openjarvis.tools._stubs import BaseTool

                tool_instances = []
                for tname in tool_names_list:
                    if ToolRegistry.contains(tname):
                        tcls = ToolRegistry.get(tname)
                        if isinstance(tcls, type) and issubclass(tcls, BaseTool):
                            tool_instances.append(tcls())
                        elif isinstance(tcls, BaseTool):
                            tool_instances.append(tcls)
                if tool_instances:
                    kwargs["tools"] = tool_instances
            kwargs["max_turns"] = config.agent.max_turns
            kwargs["interactive"] = True
            kwargs["confirm_callback"] = lambda prompt: True
        agent = agent_cls(engine, model, **kwargs)

    def on_command(text: str) -> str:
        console.print(f"[dim]Heard:[/dim] {text}")
        if agent is not None:
            response = agent.run(text)
            content = (
                response.content if hasattr(response, "content") else str(response)
            )
        else:
            from openjarvis.core.types import Message, Role

            result = engine.generate(
                [Message(role=Role.USER, content=text)], model=model
            )
            content = (
                result.get("content", "") if isinstance(result, dict) else str(result)
            )
        console.print(content)
        return content

    from openjarvis.voice.listener import ListenerConfig, WakeWordListener

    listener_config = ListenerConfig(
        wake_phrases=phrases,
        silence_threshold=config.voice.silence_threshold,
        silence_duration=config.voice.silence_duration,
        max_utterance_seconds=config.voice.max_utterance_seconds,
    )

    phrase_list = ", ".join(f'"{p}"' for p in phrases)
    console.print(
        f"[green bold]Listening for:[/green bold] {phrase_list}\n"
        f"  Agent: [cyan]{agent_key or 'direct'}[/cyan]  Model: [cyan]{model}[/cyan]\n"
        "  Press Ctrl+C to stop."
    )

    listener = WakeWordListener(
        speech_backend=speech_backend,
        tts_backend=tts_backend,
        on_command=on_command,
        config=listener_config,
    )
    try:
        listener.run()
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped listening.[/dim]")
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)


__all__ = ["listen"]
