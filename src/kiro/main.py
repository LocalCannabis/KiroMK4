"""
Kiro Daemon (kirod)

Main entry point for the Kiro assistant. Runs as a persistent daemon process
that coordinates all subsystems.

Usage:
    kirod              # Start with default config
    kirod --debug      # Start with debug logging

The daemon:
1. Loads configuration
2. Initializes logging
3. Connects to database
4. Starts event bus
5. Runs main event loop
6. Handles graceful shutdown on SIGINT/SIGTERM
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from typing import NoReturn

from kiro import __version__
from kiro.config import get_config, KiroConfig
from kiro.events import get_event_bus, EventBus
from kiro.models import init_database, close_database
from kiro.utils.logging import setup_logging, get_logger


class KiroDaemon:
    """
    Main Kiro daemon coordinator.

    Manages lifecycle of all subsystems and handles signals.
    """

    def __init__(self, config: KiroConfig):
        self.config = config
        self.logger = get_logger("kiro.daemon")
        self.event_bus: EventBus | None = None
        self._voice_pipeline = None
        self._shutdown_event = asyncio.Event()
        self._running = False

    async def start(self) -> None:
        """Start all subsystems."""
        self.logger.info(
            "kiro_starting",
            version=self.config.kiro.version,
            log_level=self.config.log.level,
        )

        # Initialize database
        self.logger.debug("initializing_database", driver=self.config.database.driver)
        await init_database(
            url=self.config.database.url,
            echo=self.config.database.echo,
        )
        self.logger.info("database_ready", path=str(self.config.database.path))

        # Start event bus
        self.event_bus = get_event_bus()
        await self.event_bus.start()
        self.logger.info("event_bus_started")

        # Start voice pipeline if audio enabled
        if self.config.audio.enabled:
            await self._start_voice_pipeline()

        # Emit startup event
        await self.event_bus.emit("kiro.started", {
            "version": self.config.kiro.version,
        })

        self._running = True
        self.logger.info("kiro_ready", message="Kiro is ready and listening")

    async def _start_voice_pipeline(self) -> None:
        """Initialize and start the full voice pipeline."""
        from kiro.voice import VoicePipeline

        self.logger.info("voice_pipeline_initializing")

        self._voice_pipeline = VoicePipeline(
            event_bus=self.event_bus,
            config=self.config,
        )

        await self._voice_pipeline.start()

    async def stop(self) -> None:
        """Stop all subsystems gracefully."""
        if not self._running:
            return

        self.logger.info("kiro_shutting_down")
        self._running = False

        # Emit shutdown event
        if self.event_bus:
            try:
                await asyncio.wait_for(
                    self.event_bus.emit("kiro.stopping", {}),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                self.logger.warning("shutdown_event_timeout")

        # Stop voice pipeline with timeout
        if self._voice_pipeline:
            try:
                await asyncio.wait_for(
                    self._voice_pipeline.stop(),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                self.logger.warning("voice_pipeline_stop_timeout")
            self._voice_pipeline = None

        # Stop event bus
        if self.event_bus:
            try:
                await asyncio.wait_for(self.event_bus.stop(), timeout=2.0)
            except asyncio.TimeoutError:
                self.logger.warning("event_bus_stop_timeout")
            self.logger.debug("event_bus_stopped")

        # Close database
        await close_database()
        self.logger.debug("database_closed")

        self.logger.info("kiro_stopped", message="Kiro shutting down...")

    async def run(self) -> None:
        """Run the main daemon loop."""
        try:
            await self.start()

            # Main loop - wait for shutdown signal
            while self._running:
                try:
                    # Check for shutdown every 100ms for responsiveness
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=0.1,
                    )
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    # Normal timeout, continue loop
                    pass

        except Exception as e:
            self.logger.error("daemon_error", error=str(e), exc_info=True)
            raise
        finally:
            await self.stop()

    def request_shutdown(self) -> None:
        """Request graceful shutdown."""
        self.logger.info("shutdown_requested")
        self._shutdown_event.set()


def setup_signal_handlers(daemon: KiroDaemon, loop: asyncio.AbstractEventLoop) -> None:
    """Set up signal handlers for graceful shutdown."""
    signal_count = [0]

    def signal_handler(sig: signal.Signals) -> None:
        signal_count[0] += 1
        daemon.logger.info("signal_received", signal=sig.name, count=signal_count[0])
        
        if signal_count[0] >= 3:
            # Force exit after 3 signals
            daemon.logger.warning("force_exit", message="Multiple signals received, forcing exit")
            import os
            os._exit(1)
        
        daemon.request_shutdown()

    # Handle SIGINT (Ctrl+C) and SIGTERM
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler, sig)


async def async_main(config: KiroConfig) -> int:
    """Async entry point."""
    daemon = KiroDaemon(config)

    # Set up signal handlers
    loop = asyncio.get_running_loop()
    setup_signal_handlers(daemon, loop)

    try:
        await daemon.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        return 1


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        prog="kirod",
        description="Kiro Daemon - Voice-first AI assistant",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Use pretty console logging instead of JSON",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Disable audio pipeline (for testing)",
    )

    return parser.parse_args()


def main() -> NoReturn:
    """Main entry point for kirod command."""
    args = parse_args()

    # Load configuration
    config = get_config()

    # Override config from command line
    if args.debug:
        config.log.level = "DEBUG"
    if args.console:
        config.log.format = "console"
    if args.no_audio:
        config.audio.enabled = False

    # Set up logging
    setup_logging(
        level=config.log.level,
        format=config.log.format,
        log_file=config.log.file,
    )

    # Run daemon
    exit_code = asyncio.run(async_main(config))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
