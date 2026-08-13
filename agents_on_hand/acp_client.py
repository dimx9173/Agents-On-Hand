import asyncio
import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)



class ACPClient:
    """
    Async JSON-RPC 2.0 Client over stdio subprocess for ACP (Agent Client Protocol).
    """

    def __init__(self, command: str, working_dir: str):
        self.command = command
        self.working_dir = working_dir
        self.process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0
        self._pending_requests: dict = {}
        self._listeners = []
        self._permission_listeners = []
        self._read_task: Optional[asyncio.Task] = None
        self.is_running = False

    def register_listener(self, callback):
        """Register listener for ACP notifications (updates, content deltas, etc.)."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def register_permission_listener(self, callback):
        """Register listener for ACP permission requests (tool call approvals)."""
        if callback not in self._permission_listeners:
            self._permission_listeners.append(callback)

    async def start(self):
        """Spawn the ACP stdio process and start reading JSON-RPC lines."""
        cmd_parts = self.command.split()
        logger.info(f"Spawning ACP process: {cmd_parts} in cwd={self.working_dir}")

        self.process = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.working_dir,
            limit=10 * 1024 * 1024,  # 10MB line limit for large ACP JSON-RPC payloads
        )

        self.is_running = True
        self._read_task = asyncio.create_task(self._read_loop())

        # Perform ACP Initialize handshake
        init_res = await self.call_method(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {"name": "AgentsOnHand", "version": "1.0"},
            },
            timeout=8.0,
        )

        logger.info(f"ACP Initialize handshake successful: {init_res}")

        # Create ACP Session via session/new
        sess_res = await self.call_method(
            "session/new",
            {
                "cwd": str(self.working_dir),
                "mcpServers": [],
            },
            timeout=10.0,
        )

        if isinstance(sess_res, dict) and "sessionId" in sess_res:
            self.acp_session_id = sess_res["sessionId"]
            logger.info(f"ACP Session created successfully with sessionId={self.acp_session_id}")
        else:
            logger.warning(f"session/new did not return sessionId: {sess_res}")

        return init_res

    async def _read_loop(self):
        """Read stdout JSON lines continuously."""
        while self.is_running and self.process and self.process.stdout:
            try:
                line = await self.process.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    data = json.loads(line_str)
                    self._handle_json_msg(data)
                except json.JSONDecodeError:
                    logger.debug(f"Non-JSON output from ACP process: {line_str}")
            except asyncio.CancelledError:
                break
            except (ValueError, asyncio.LimitOverrunError) as e:
                logger.warning(f"ACP stdio line exceeded buffer limit: {e}. Skipping chunk...")
                try:
                    # Attempt to read remaining chunk up to newline
                    await self.process.stdout.readuntil(b"\n")
                except Exception:
                    pass
                continue
            except Exception as e:
                logger.error(f"Error in ACP read loop: {e}")
                break

        self.is_running = False

    def _handle_json_msg(self, data: dict):
        """Process incoming JSON-RPC response, request, or notification."""
        # Case 1: Response to client request
        if "id" in data and ("result" in data or "error" in data):
            req_id = data["id"]
            if req_id in self._pending_requests:
                fut = self._pending_requests.pop(req_id)
                if not fut.done():
                    if "error" in data:
                        fut.set_exception(RuntimeError(data["error"]))
                    else:
                        fut.set_result(data.get("result"))
            return

        # Case 2: Incoming Notification from Agent (e.g. agent/update)
        method = data.get("method", "")
        params = data.get("params", {})

        if method in ("agent/update", "session/update", "notification"):
            for listener in self._listeners:
                try:
                    listener(params)
                except Exception as e:
                    logger.error(f"Error in ACP update listener: {e}")

        # Case 3: Permission Request from Agent (tool execution approval)
        elif method in ("agent/request_permission", "permission/request", "request_approval"):
            req_id = data.get("id")
            tool_name = params.get("name") or params.get("title") or "unknown"
            logger.info(
                f"ACP permission_request received: req_id={req_id} tool='{tool_name}' "
                f"— waiting for user approval"
            )
            for perm_listener in self._permission_listeners:
                try:
                    perm_listener(req_id, params)
                except Exception as e:
                    logger.error(f"Error in ACP permission listener: {e}")


    async def call_method(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        """Call a JSON-RPC method on the ACP server and return result."""
        if not self.process or not self.process.stdin:
            raise RuntimeError("ACP process is not running")

        self._request_id += 1
        req_id = self._request_id
        req_payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = fut

        msg_bytes = (json.dumps(req_payload) + "\n").encode("utf-8")
        self.process.stdin.write(msg_bytes)
        await self.process.stdin.drain()

        _t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
            elapsed = time.monotonic() - _t0
            logger.debug(f"ACP call '{method}' (req_id={req_id}) completed in {elapsed:.3f}s")
            return result
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - _t0
            logger.error(
                f"ACP call '{method}' (req_id={req_id}) TIMED OUT after {elapsed:.3f}s "
                f"(timeout={timeout}s)"
            )
            raise


    async def send_notification(self, method: str, params: dict):
        """Send a JSON-RPC notification (no response expected)."""
        if not self.process or not self.process.stdin:
            return

        req_payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        msg_bytes = (json.dumps(req_payload) + "\n").encode("utf-8")
        self.process.stdin.write(msg_bytes)
        await self.process.stdin.drain()

    async def respond_to_permission(self, request_id: int, approved: bool):
        """Respond to an agent's permission_request with approved / rejected."""
        if not self.process or not self.process.stdin:
            return

        res_payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"approved": approved},
        }
        msg_bytes = (json.dumps(res_payload) + "\n").encode("utf-8")
        self.process.stdin.write(msg_bytes)
        await self.process.stdin.drain()

    async def prompt(self, prompt_text: str):
        """Send a user prompt to the ACP Agent session."""
        params = {
            "prompt": [{"type": "text", "text": prompt_text}]
        }
        if hasattr(self, "acp_session_id") and self.acp_session_id:
            params["sessionId"] = self.acp_session_id

        return await self.call_method(
            "session/prompt",
            params,
            timeout=120.0,
        )


    async def cancel(self):
        """Cancel current session execution."""
        await self.send_notification("session/cancel", {})

    def stop(self):
        """Stop/kill the ACP subprocess synchronously/asyncio task."""
        self.is_running = False
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass

