from uuid import uuid4


def new_session_id() -> str:
    return f"session_{uuid4().hex}"


def new_run_id() -> str:
    return f"run_{uuid4().hex}"
