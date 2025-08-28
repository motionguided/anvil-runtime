import collections, json, os, psutil, random, signal, subprocess, sys, threading, time, traceback, platform
from datetime import datetime

from ws4py.client.threadedclient import WebSocketClient

from anvil_downlink_util.tracing import trace, context, deserialise_parent_ctx, serialise_span_ctx, AnvilRpcExporter, get_tracer_provider
trace.set_tracer_provider(get_tracer_provider("anvil-downlink-host", AnvilRpcExporter(lambda: connection and connection.authenticated, lambda msg: connection.send_with_header(msg))))
tracer = trace.get_tracer(__name__)

import anvil_downlink_host.memory as memory
# Configuration

TIMEOUT = int(os.environ.get("DOWNLINK_WORKER_TIMEOUT", "30"))
BACKGROUND_TIMEOUT = int(os.environ.get("DOWNLINK_BACKGROUND_TIMEOUT", "0"))
KEEPALIVE_TIMEOUT = int(os.environ.get("DOWNLINK_KEEPALIVE_TIMEOUT", "30"))
DROP_PRIVILEGES = os.environ.get("DROP_PRIVILEGES")
RUNTIME_ID = os.environ.get("RUNTIME_ID", None) or ('python2-full' if sys.version_info[0] < 3 else 'python3-full')
USER_ID = os.environ.get("DOWNLINK_USER_ID", None)
ORG_ID = os.environ.get("DOWNLINK_ORG_ID", None)
ENV_ID = os.environ.get("DOWNLINK_ENV_ID", None)
SPEC_HASH = os.environ.get("DOWNLINK_SPEC_HASH", None)
APP_CACHE_SIZE = int(os.environ.get("APP_CACHE_SIZE", "16"))
ENABLE_PDF_RENDER = os.environ.get("ENABLE_PDF_RENDER")
PER_WORKER_SOFT_MEMORY_LIMIT = int(os.environ["PER_WORKER_SOFT_MEMORY_LIMIT_MB"])*1024*1024 \
                                    if "PER_WORKER_SOFT_MEMORY_LIMIT_MB" in os.environ else None
IDLE_TIMEOUT_SECONDS = int(os.environ.get("IDLE_TIMEOUT_SECONDS","0"))
MAX_WEBSOCKET_PAYLOAD = int(os.environ.get("MAX_WEBSOCKET_PAYLOAD", "16777216"))

IS_WINDOWS = "Windows" in platform.system() or "CYGWIN" in platform.system()

for V in ["DOWNLINK_WORKER_TIMEOUT", "DROP_PRIVILEGES", "RUNTIME_ID", "DOWNLINK_USER_ID", "DOWNLINK_ORG_ID", "APP_CACHE_SIZE", "ENABLE_PDF_RENDER"]:
    if V in os.environ:
        del os.environ[V]

# Worker modules register themselves here
workers_by_id = {}

# Cache app content
app_cache = collections.OrderedDict()


def send_with_header(json_data, blob=None, on_oversize=None):
    """"Send data to the API router"""
    # print("<< ", str(json_data))
    # if blob is not None:
    #     print("<< [", len(blob), " bytes]")
    connection.send_with_header(json_data, blob, on_oversize=on_oversize)


def report_oversize_response(json_data):
    """This function is used for reporting oversize responses up the websocket.
       Pass as on_oversize= to send_with_header()"""
    send_with_header({
        "id": json_data.get("id"),
        "error": {
            "type": "anvil.server.SerializationError",
            "message": "This function returned too much data - please use Media objects to transfer large amounts of data."
        }
    })


def truncate_oversize_output(json_data):
    output = json_data.get("output", "<output missing>")
    send_with_header({
        "id": json_data.get("id"),
        "output": "[OVERSIZED OUTPUT TRUNCATED ({} chars)]: {}[...]".format(len(output), output[:256])
    })


# Host state

launch_worker = None
launch_pdf_worker = None
retire_cached_worker = lambda w: None

connection = None

rnd = random.SystemRandom()
MY_SESSION_ID = "".join((rnd.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(20)))

draining_start_time = None


def is_idle():
    return len(workers_by_id) == 0


def maybe_quit_if_draining_and_done():
    if draining_start_time is not None and is_idle():
        if time.time() < draining_start_time + 10:
            print("Giving API 10 seconds' grace for drain...")
            def f():
                time.sleep(draining_start_time + 10 - time.time())
                maybe_quit_if_draining_and_done()
            t = threading.Thread(target=f)
            t.daemon = True
            t.start()
        else:
            print("Drain complete. Exiting.")
            os._exit(0)


# Utility functions

def get_demote_fn(app_id):
    if os.name == "nt":
        return None

    # TODO: Use app_id here to seed UID generation. It might be an actual app ID, or None
    uid = 20000
    def do_demotion():
        if DROP_PRIVILEGES and os.getuid() == 0:
            os.setgroups([])
            os.setgid(uid)
            os.setegid(uid)
            os.setuid(uid)
            os.seteuid(uid)

        # Give ourselves an isolated process group so we can take child processes with us when we go
        os.setpgid(0, 0)

    return do_demotion


class PopenWithGroupKill(subprocess.Popen):
    def terminate(self):
        try:
            os.killpg(self.pid, 9)
        except:
            pass
        super(PopenWithGroupKill, self).terminate()


# Handle communication with API router
class Connection(WebSocketClient):
    def __init__(self, url, key):
        print("Connecting to " + url)
        WebSocketClient.__init__(self, url)

        self._sending_lock = threading.RLock()
        self._send_next_bin = None
        self._key = key
        self._last_keepalive_reply = time.time()
        self._last_activity = time.time()
        self._idle_timeout_timer = None
        self._authenticated = False
        self._authenticated_condition = threading.Condition()

        t = threading.Timer(30, self.check_keepalives)
        t.daemon = True
        t.start()

    def record_activity(self):
        self._last_activity = time.time()

    def reset_idle_timer(self):
        if self._idle_timeout_timer:
            self._idle_timeout_timer.cancel()
        if IDLE_TIMEOUT_SECONDS:
            self._idle_timeout_timer = threading.Timer(IDLE_TIMEOUT_SECONDS, self.idle_timeout)
            self._idle_timeout_timer.daemon = True
            self._idle_timeout_timer.start()

    def idle_timeout(self):
        if is_idle() and self._last_activity < time.time() - IDLE_TIMEOUT_SECONDS:
            print("Idle timeout")
            signal_drain()
        else:
            self.reset_idle_timer()

    def check_keepalives(self):
        if time.time() - max(self._last_keepalive_reply, self._last_activity) > KEEPALIVE_TIMEOUT:
            print("No keepalive reply or activity in %s seconds. Exiting." % KEEPALIVE_TIMEOUT)
            os._exit(1)
        else:
            t = threading.Timer(30, self.check_keepalives)
            t.daemon = True
            t.start()

    @property
    def authenticated(self):
        return self._authenticated

    def wait_for_authentication(self):
        self._authenticated_condition.acquire()
        try:
            while not self._authenticated:
                self._authenticated_condition.wait()
        finally:
            self._authenticated_condition.release()

    def opened(self):
        print("Anvil websocket open")
        self.record_activity()
        spec = {
            'runtime': RUNTIME_ID,
            'session_id': MY_SESSION_ID,
        }

        if USER_ID is not None:
            spec['user_id'] = USER_ID
        elif ORG_ID is not None:
            spec['org_id'] = ORG_ID
        elif ENV_ID is not None:
            spec['env_id'] = ENV_ID
            spec['spec_hash'] = SPEC_HASH

        id = os.environ.get("DOWNLINK_ID", None)
        if id:
            spec['id'] = id
        self.send(json.dumps({
            'key': self._key,
            'v': 2,
            'spec': spec,
        }))

    def closed(self, code, reason=None):
        print("Anvil websocket closed (code %s, reason=%s)" % (code, reason))
        # The world has ended. Let whatever is in charge of restarting us sort it out.
        os._exit(1)

    def received_message(self, message):
        try:
            self._received_message(message)
        except Exception as e:
            print("Error in received_message():")
            traceback.print_exc()
            raise

    def _received_message(self, message):
        if message.is_binary:
            memory.count("MEDIA FROM PLATFORM SERVER (BYTES)", len(message.data))
            memory.count("MEDIA FROM PLATFORM SERVER (CHUNKS)", 1)
            # print(">>> [", len(message.data), " bytes]")
            self._send_next_bin(message.data)

        else:
            data = json.loads(message.data.decode())
            memory.count("JSON FROM PLATFORM SERVER (BYTES)", len(message.data))
            memory.count("JSON FROM PLATFORM SERVER (MESSAGES)", 1)
            # print(">>> ", str(data))

            type = data["type"] if 'type' in data else None
            id = data["id"] if 'id' in data else None
            is_keepalive = id and id.startswith("downlink-keepalive")

            ### TODO: Deserialise sts here.

            if not is_keepalive:
                self.record_activity()

            if 'auth' in data:
                print("Downlink authenticated OK")
                self._authenticated_condition.acquire()
                try:
                    self._authenticated = True
                    self._authenticated_condition.notify_all()
                finally:
                    self._authenticated_condition.release()
                self.reset_idle_timer()

            elif 'output' in data:
                # Output from something this worker has called.
                calling_worker = workers_by_id.get(data.get('id'))
                originating_call = calling_worker.outbound_ids.get(id) if calling_worker is not None else None

                if originating_call is not None:
                    data['id'] = originating_call
                    self.send_with_header(data)
                else:
                    print("Bogus output, probably for an old request (worker: %s): %s" %
                          ("FOUND" if calling_worker else "MISSING", repr(data)[:100]))

            elif type in ["CALL", "LAUNCH_BACKGROUND", "LAUNCH_REPL"]:

                if "app" not in data:
                    cached_app = app_cache.get((data["app-id"], data["app-version"]))
                    if cached_app is not None:
                        #print("Filling out app from cache for %s" % ((data["app-id"], data["app-version"]),))
                        data["app"] = cached_app

                #print "Launching new worker for ID " + id
                if draining_start_time and data.get("command", None) != "anvil.private.pdf.get_component" and \
                        time.time() > draining_start_time + 10:
                    self.send_with_header({"id": id, "error": {"type": "anvil.server.DownlinkDrainingError", "message": "New call routed to draining downlink"}})
                else:
                    if data.get("command", None) == "anvil.private.pdf.do_print":
                        if launch_pdf_worker:
                            launch_pdf_worker(data)
                        else:
                            self.send_with_header({"id": id, "error": {"type": "anvil.server.RuntimeUnavailableError", "message": "PDF Rendering unavailable"}})
                    else:
                        launch_worker(data)

                #print "Launched"

            elif type in ["REPL_COMMAND", "REPL_KEEPALIVE", "TERMINATE_REPL"]:
                worker = workers_by_id.get(data['repl'])

                # TODO allow REPL commands to be run on us too

                if worker is not None:
                    worker.handle_inbound_message(data)
                else:
                    print("Couldn't find repl %s; current workers: %s" % (data['repl'], workers_by_id.keys()))
                    connection.send_with_header(
                        {'error': {'type': 'anvil.server.NotRunningTask', 'message': 'No such REPL running'},
                         'id': data['id']}
                    )

            elif type == "KILL_TASK":

                worker = workers_by_id.get(data['task'])
                if worker is not None:
                    worker.kill_background_task()

            elif type == "GET_TASK_STATE":

                worker = workers_by_id.get(data['task'])
                if worker is not None:
                    worker.get_task_state(data)
                else:
                    connection.send_with_header(
                        {'error': {'type': 'anvil.server.NotRunningTask', 'message': 'No such task running'},
                         'id': data['id']})

            elif type == "DEBUG_REQUEST":
                worker = workers_by_id.get(data['debugger-id'])
                if worker is not None:
                    worker.handle_debug_request(data)
                else:
                    connection.send_with_header(
                        {'error': {'type': 'anvil.server.NoSuchDebugger', 'message': 'No such debugger running'},
                         'id': data['id']})


            elif type == "SET_IDLE_TIMEOUT":
                global IDLE_TIMEOUT_SECONDS
                IDLE_TIMEOUT_SECONDS = data['timeout']
                self.reset_idle_timer()
                print("Resetting idle timeout to", data['timeout'])

            elif type == "CHUNK_HEADER":

                if data.get('lastChunk'):
                    memory.count("MEDIA FROM PLATFORM SERVER (OBJECTS)", 1)

                if data['requestId'] in workers_by_id:
                    worker = workers_by_id[data['requestId']]

                    def send_next_bin(bin_data):
                        worker.handle_inbound_message(data, bin_data)
                        self._send_next_bin = None

                    self._send_next_bin = send_next_bin
                else:
                    print("Ignoring media for unknown request %s" % data['requestId'])
                    self._send_next_bin = lambda x: 0

            elif type == "MEDIA_ERROR":
                worker = workers_by_id.get(data['requestId'])
                if worker is not None:
                    worker.handle_inbound_message(data)

            elif (type is None or type == "PROVIDE_APP") and "id" in data:
                if type == "PROVIDE_APP":
                    #print("PROVIDE_APP: Cache fill for %s" % ((data["app-id"], data["app-version"]),))
                    app_cache[(data["app-id"], data["app-version"])] = data["app"]
                    if len(app_cache) > APP_CACHE_SIZE:
                        app_cache.popitem(False)

                if id in workers_by_id:
                    workers_by_id[id].handle_inbound_message(data)
                elif is_keepalive:
                    self._last_keepalive_reply = time.time()
                else:
                    print("Bogus reply for " + id + ": " + repr(data)[:120])

            elif type is None and "error" in data:
                print("Fatal error from Anvil server: " + str(data["error"]))
                os._exit(1)
            else:
                print("Anvil websocket got unrecognised message: "+repr(data))

    def send(self, payload, binary=False):
        with self._sending_lock:
            return WebSocketClient.send(self, payload, binary)

    def get_task_state(self, msg):
        raise NotImplemented

    def handle_debug_request(self, msg):
        raise NotImplemented

    def send_with_header(self, json_data, blob=None, on_oversize=None):
        if (not json_data.get("id","").startswith("downlink-keepalive")) and json_data.get("type") not in ["STATS", "TRACE"]:
            self.record_activity()
        bin = json.dumps(json_data)
        if len(bin) >= MAX_WEBSOCKET_PAYLOAD:
            if on_oversize:
                on_oversize(json_data)
                return
            else:
                print("Oversized payload, websocket will die shortly: " + bin[:128] + "...")
        with self._sending_lock:
            memory.count("JSON FROM WORKER (BYTES)", len(bin))
            memory.count("JSON FROM WORKER (MESSAGES)", 1)
            WebSocketClient.send(self, bin, False)
            if blob is not None:
                memory.count("MEDIA FROM WORKER (BYTES)", len(blob))
                memory.count("MEDIA FROM WORKER (CHUNKS)", 1)
                if json_data.get("lastChunk"):
                    memory.count("MEDIA FROM WORKER (OBJECTS)", 1)
                WebSocketClient.send(self, blob, True)


# Defined in two places, so it can be used by BaseWorker and the full-python worker. Yeuch.
def report_worker_stats(self):
    p = self.proc_info
    if p is None:
        return {}
    try:
        cpu = p.cpu_times()
        mem = p.memory_full_info()
        return {
            "info": self.task_info,
            "age":  time.time() - p.create_time(),
            "cpu": {
                "user": cpu.user + cpu.children_user,
                "system": cpu.system + cpu.children_system,
                "total": cpu.user + cpu.system + cpu.children_user + cpu.children_system
            },
            "mem": {"vms": mem.vms, "uss": mem.uss},
        }
    except psutil.Error:
        return {}


# Shared tools for managing worker processes.
# Nomenclature: "Inbound" calls come from the API server. "Outbound" calls come from the server.
class BaseWorker(object):
    def __init__(self, initial_msg, task_info):
        self.req_ids = set()
        self.outbound_ids = {} # Outbound ID -> inbound ID it came from
        self._media_tracking = {} # reqID -> (set([mediaId, mediaId, ]), finishedCallback)
        self.start_times = {}
        self.parent_spans = {}
        self.spans = {}
        self.proc_info = None
        self.task_info = task_info
        self._dead_with_error = None # set this flag to reject all future attempts to add to req_ids

        self.initial_req_id = initial_msg['id']

    # Handle bookkeeping for which requests we're handling and waiting for

    def record_outbound_call_started(self, outbound_msg):
        outbound_id = outbound_msg['id']
        if outbound_id in workers_by_id:
            raise Exception("Duplicate ID: %s" % outbound_id)

        self.outbound_ids[outbound_msg['id']] = outbound_msg.get('originating-call', self.initial_req_id)
        if 'span-ctx' in outbound_msg:
            ctx = deserialise_parent_ctx(outbound_msg['span-ctx'])
        else:
            ctx = trace.set_span_in_context(self.spans.get(self.outbound_ids[outbound_msg['id']]))
        span = tracer.start_span("Outbound call from downlink: {}".format(outbound_msg.get('command', outbound_msg.get('type'))), ctx)
        self.spans[outbound_id] = span
        outbound_msg['span-ctx'] = serialise_span_ctx(span)
        workers_by_id[outbound_id] = self
        
        # We don't need a callback when media transfer is complete, but we do want to track media we're sending,
        # in case we time out and need to shoot down the transfer.
        self.on_media_complete(outbound_msg, lambda: None)

    def record_outbound_call_complete(self, outbound_id):
        self.outbound_ids.pop(outbound_id, None)
        workers_by_id.pop(outbound_id, None)
        s = self.spans.pop(outbound_id, None)
        if s:
            s.end()
        maybe_quit_if_draining_and_done()

    def record_inbound_call_started(self, inbound_msg):
        inbound_id = inbound_msg['id']

        if self._dead_with_error:
            sys.stderr.write("Late inbound call to dead worker: " + self._dead_with_error['message'] + " (IDs %s)\n" % inbound_id)
            sys.stderr.flush()
            send_with_header({'id': inbound_id, 'error': self._dead_with_error})
        elif inbound_id in self.req_ids:
            return # Otherwise worker initial_msg gets processed twice.
        else:
            self.req_ids.add(inbound_id)
            self.start_times[inbound_id] = time.time()
            
            ctx = deserialise_parent_ctx(inbound_msg.get("span-ctx"))
            span = tracer.start_span("Inbound downlink call: {}".format(inbound_msg.get('command')), ctx)
            self.spans[inbound_id] = span
            inbound_msg["span-ctx"] = serialise_span_ctx(span)
            context.attach(trace.set_span_in_context(span))
            
            workers_by_id[inbound_id] = self
            # Don't bother tracking media in inbound call args, because if we timeout or
            # otherwise die, the incoming media will still happily arrive and be discarded.

    def record_inbound_call_complete(self, inbound_id):
        self.req_ids.discard(inbound_id)
        self.start_times.pop(inbound_id, None)
        span = self.spans.pop(inbound_id, None)
        if span:
            span.end()
        workers_by_id.pop(inbound_id, None)

        if len(self.req_ids) == 0:
            self.on_all_inbound_calls_complete()

        maybe_quit_if_draining_and_done()

    def report_abandoned_media_transfers(self, id, error=None):
        media_ids, _ = self._media_tracking.pop(id, ([], None))
        if media_ids:
            send_with_header({"type": "MEDIA_ERROR", "requestId": id, "mediaIds": list(media_ids), "cause": error})

    def report_dead(self, error, print_info=False):
        """Report the specified error for all future attempts to record inbound calls"""
        self._dead_with_error = error
        for i in self.req_ids:
            if print_info:
                sys.stderr.write(error['message'] + " (IDs %s)\n" % i)
                sys.stderr.flush()
            send_with_header({'id': i, 'error': error})

    def clean_up_all_outstanding_records(self, err=None):
        for id in self.req_ids:
            self.report_abandoned_media_transfers(id, err)
            workers_by_id.pop(id, None)
            self.spans.pop(id, None)
        for id in self.outbound_ids:
            self.report_abandoned_media_transfers(id, err)
            workers_by_id.pop(id, None)
            self.spans.pop(id, None)

    def ensure_id_is_mine(self, req_id):
        if not (req_id in self.req_ids or req_id in self.outbound_ids):
            raise Exception("Worker attempted to send an ID that doesn't belong to it")

    # Events to be overridden by children

    def handle_inbound_message(self, msg, bindata=None):
        raise Exception("handle_inbound_message() not implemented")

    def on_all_inbound_calls_complete(self):
        raise Exception("on_all_inbound_calls_complete() not implemented")

    def repl_keepalive(self):
        raise Exception("repl_keepalive() not implemented")

    # A common task is to track when the worker has finished sending media for a particular request,
    # so we can safely kill it.

    def on_media_complete(self, msg, callback):
        """Register a callback to execute when the worker has finished sending all the media in the given message."""
        media_ids = set()
        for o in msg.get("objects", []):
            if "DataMedia" in o.get("type", []):
                media_ids.add(o["id"])
        if len(media_ids) == 0:
            callback()
        else:
            # print("Waiting for media for request '%s': %s" % (msg['id'], repr(list(media_ids))))
            self._media_tracking[msg['id']] = (media_ids, callback)

    def transmitted_media(self, request_id, media_id):
        """The worker has finished sending the specified media object; call any necessary callbacks"""

        # print("Media complete: '%s', '%s'" % (request_id, media_id))
        if request_id in self._media_tracking:
            media_ids, callback = self._media_tracking[request_id]
            media_ids.discard(media_id)
            if len(media_ids) == 0:
                callback()
                del self._media_tracking[request_id]

    # Slightly awkward shimming of profiling information into a response message
    def fill_out_profiling(self, response_msg, description="Downlink dispatch"):
        """Add profiling information to a response message"""

        p = response_msg.get("profile", None)
        response_msg["profile"] = {
            "origin": "Server (Python)",
            "description": description,
            "start-time": float(self.start_times.get(response_msg['id'], 0)*1000),
            "end-time": float(time.time()*1000),
            "span-ctx": self.parent_spans.get(response_msg['id']),
        }
        if p is not None:
            response_msg["profile"]["children"] = [p]

            for o in response_msg.get("objects", []):
                if o["path"][0] == "profile":
                    o["path"].insert(1,"children")
                    o["path"].insert(2, 0)

    report_stats = report_worker_stats


# Import the actual worker modules

def init_pdf_worker():
    global launch_pdf_worker

    if sys.version_info < (3,7,0):
        print("Warning: PDF Rendering requires Python 3.7. Renderer not initialised")
    elif IS_WINDOWS:
        print("Warning: PDF Rendering not supported on Windows. Renderer not initialised")
    else:
        from . import pdf_renderer
        pdf_renderer.init()
        launch_pdf_worker = pdf_renderer.launch


if RUNTIME_ID == "pdf-renderer":
    init_pdf_worker()
elif RUNTIME_ID.endswith('-sandbox') and not os.getenv("FAKE_SANDBOX"):
    from . import pypy_sandbox
    launch_worker = pypy_sandbox.launch
else:
    from . import full_python
    launch_worker = full_python.launch

    if ENABLE_PDF_RENDER:
        init_pdf_worker()


def signal_drain(_signum=None, _frame=None):
    global draining_start_time
    if draining_start_time:
        print("Downlink has already been draining for %s seconds. %s call(s) remaining:" % (int(time.time() - draining_start_time), len(workers_by_id)))
        print(list(workers_by_id.keys()))
    else:
        connection.send_with_header({
            "type": "DRAIN"
        })
        print("Draining downlink. %s call(s) remaining:" % len(workers_by_id))
        print(list(workers_by_id.keys()))
        draining_start_time = time.time()
        maybe_quit_if_draining_and_done()


def signal_interrupt(_signum=None, _frame=None):
    raise KeyboardInterrupt()


def signal_terminate(_signum=None, _frame=None):
    print("Downlink terminated")
    os._exit(0)


def report_stats():
    workers = set(workers_by_id.values())
    worker_stats = []
    for worker in workers:
        stats = worker.report_stats()
        mem_usage = stats.get("mem", {}).get("uss", 0)
        if PER_WORKER_SOFT_MEMORY_LIMIT is not None and mem_usage > PER_WORKER_SOFT_MEMORY_LIMIT:
            print("Worker is using %.0fMB, retiring: %s" % (mem_usage/(1024*1024.0), stats.get('info')))
            retire_cached_worker(worker)
        worker_stats.append(stats)
    connection.send_with_header({
        "type": "STATS",
        "data": worker_stats
    })


def posix_utc_date_to_timestamp_nanos(s):
    """
    Python 3.10 is unable to parse the ISO 8601 timestamp produced by posix date.
    This is the bare minimum needed to parse the output of `date -In -u`.
    """
    ts, rest = s.split(",", 1)
    micros = rest[:6]
    return int(1e9 * (datetime.strptime(ts + "." + micros, "%Y-%m-%dT%H:%M:%S.%f") - datetime(1970, 1, 1)).total_seconds())


def run_downlink_host():
    global connection

    startup_ctx_json = os.environ.get("TRACING_STARTUP_CONTEXT")
    ctx = deserialise_parent_ctx(json.loads(startup_ctx_json)) if startup_ctx_json else None

    copy_sources_timestamp_file = os.environ.get("COPY_SOURCES_TIMESTAMP_FILE")
    if copy_sources_timestamp_file:
        with open(copy_sources_timestamp_file, "r") as f:
            start_time = posix_utc_date_to_timestamp_nanos(f.readline().strip())
            end_time = posix_utc_date_to_timestamp_nanos(f.readline().strip())

        tracer.start_span("Copy Sources", ctx, start_time=start_time).end(end_time)

    with tracer.start_as_current_span("Initialise Downlink Host", ctx):

        url = os.environ.get("DOWNLINK_SERVER", "ws://localhost:3000/downlink")
        key = os.environ.get("DOWNLINK_KEY", "ZeXiedeaceimahm1ePhaguvu5Ush9E")
        os.environ['TZ'] = 'UTC'

        for v in ["DOWNLINK_SERVER", "DOWNLINK_KEY"]:
            if v in os.environ:
                del os.environ[v]

        with tracer.start_as_current_span("Connect"):
            connection = Connection(url, key)
            connection.connect()

        with tracer.start_as_current_span("Authenticate"):
            connection.wait_for_authentication()

    AnvilRpcExporter.flush_all()

    if not IS_WINDOWS:
        try:
            signal.signal(signal.SIGUSR2, signal_drain)
            # Also add default handlers so we can run as PID 1.
            # This doesn't make us into a fully-compliant init process,
            # but it does mean we shut down gracefully in a container.
            signal.signal(signal.SIGINT,  signal_interrupt)
            signal.signal(signal.SIGTERM, signal_terminate)
        except Exception as e:
            print("Failed to add signal handler: %s" % e)

    n = 0
    while True:
        try:
            for _ in range(2):
                time.sleep(5)
                report_stats()

            connection.send_with_header({
                "type": "CALL",
                "id": "downlink-keepalive-%d" % n,
                "command": "anvil.private.echo",
                "args": ["keep-alive"],
                "kwargs": {},
            })
            n += 1
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("Keepalive failed. The downlink has probably disconnected.")
            print(e)
            os._exit(1)

    print("Downlink shutting down")