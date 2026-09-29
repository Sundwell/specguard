import contextlib
import fcntl
import json
import os
import time
import traceback

LOCK_TIMEOUT_S = 2.0


def compute_state_dir(project_dir):
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    key = os.path.abspath(project_dir).replace(os.sep, "-")
    return os.path.join(base, "specguard", key)


@contextlib.contextmanager
def _locked(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = path + ".lock"
    fh = open(lock_path, "a+")
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("state lock {} busy for {} s".format(lock_path, LOCK_TIMEOUT_S))
                time.sleep(0.02)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def _atomic_write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp.{}".format(os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


class State:
    def __init__(self, project_dir):
        self.project_dir = project_dir
        self.dir = compute_state_dir(project_dir)

    def path(self, name):
        return os.path.join(self.dir, name)

    def read_json(self, name, default=None):
        try:
            with open(self.path(name), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def update_json(self, name, fn):
        path = self.path(name)
        with _locked(path):
            current = self.read_json(name, {})
            if current is None:
                current = {}
            updated = fn(current)
            _atomic_write_json(path, updated)
            return updated

    def session(self, sid):
        return self.read_json(os.path.join("sessions", "{}.json".format(sid)), {}) or {}

    def update_session(self, sid, fn):
        return self.update_json(os.path.join("sessions", "{}.json".format(sid)), fn)

    def log(self, ev, **fields):
        record = {"t": time.time(), "ev": ev}
        record.update(fields)
        line = json.dumps(record, sort_keys=True)
        path = self.path("log.jsonl")
        with _locked(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def error(self, text):
        try:
            path = self.path("errors.log")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
                if not text.endswith("\n"):
                    f.write("\n")
        except OSError:
            pass

    def error_exc(self):
        self.error(traceback.format_exc())

    def record_agent(self, agent_id, session_id, agent_type):
        def update(agents):
            agents[agent_id] = {"session_id": session_id, "agent_type": agent_type}
            return agents

        return self.update_json("agents.json", update)

    def resolve_root(self, session_id, agent_id=None):
        if session_id:
            return session_id
        if agent_id:
            agents = self.read_json("agents.json", {}) or {}
            entry = agents.get(agent_id)
            if entry:
                return entry.get("session_id")
        return session_id

    def record_advocate_report(self, path, agent_id):
        def update(reports):
            reports.setdefault(path, [])
            reports[path].append({"agent_id": agent_id, "at": time.time()})
            return reports

        return self.update_json("advocate-reports.json", update)
