"""One durable LangGraph Supervisor delegates to an allowlisted skill runner."""
from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from .config import get_settings
from . import jobs
from .skills import run_skill


class ResearchState(TypedDict, total=False):
    job_id: str
    validated: bool
    result: dict


def execute(job_id: str, owner: str) -> tuple[dict, str | None]:
    """Resume the graph by job/thread ID; durable skill outputs prevent re-calls."""
    def validate(state: ResearchState) -> dict:
        jobs.ensure_running(job_id, owner)
        record = jobs.job_record(job_id)
        if record["input"].get("scope", "abstract") == "abstract" and not record["paper"].get("abstract"):
            raise ValueError("论文没有摘要，无法执行摘要任务")
        jobs.update_progress(job_id, owner, "输入与来源校验完成", 10)
        return {"validated": True}

    def delegate(state: ResearchState) -> dict:
        record = jobs.job_record(job_id)
        return {"result": run_skill(record, owner)}

    def persist(state: ResearchState) -> dict:
        jobs.update_progress(job_id, owner, "校验产物并持久保存", 95)
        return {}

    builder = StateGraph(ResearchState)
    builder.add_node("validate", validate)
    builder.add_node("run_skill", delegate)
    builder.add_node("persist", persist)
    builder.add_edge(START, "validate")
    builder.add_edge("validate", "run_skill")
    builder.add_edge("run_skill", "persist")
    builder.add_edge("persist", END)
    checkpoint_path = get_settings().database_path.with_name("checkpoints.sqlite")
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    config = {"configurable": {"thread_id": job_id}, "recursion_limit": 8}
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        graph = builder.compile(checkpointer=saver)
        snapshot = graph.get_state(config)
        if snapshot.values and not snapshot.next and not snapshot.values.get("result", {}).get("partial"):
            result = snapshot.values["result"]
        else:
            state = graph.invoke(None if snapshot.next else {"job_id": job_id}, config)
            result = state["result"]
        current = graph.get_state(config)
        checkpoint_id = current.config.get("configurable", {}).get("checkpoint_id") if current.config else None
    return result, checkpoint_id
