"""
BSB Multi-Agent LangGraph Pipeline
====================================
Assembles the StateGraph connecting:
  supervisor -> receptionist_agent
  and any agent can transition to any other agent based on state["next_agent"].

Usage:
    graph = build_graph(db)
    config = {"configurable": {"thread_id": call_id}}
    state  = graph.invoke(initial_state, config=config)
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from app.agents.graph_state import BSBGraphState
from app.agents.supervisor import supervisor_node

logger = logging.getLogger("bsb_graph")


def _make_checkpointer(checkpoint_path: str):
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
        saver = SqliteSaver(conn)
        logger.info(f"[BSBGraph] Using SQLite checkpointer at {checkpoint_path}")
        return saver
    except Exception as e:
        logger.warning(f"[BSBGraph] SQLite checkpointer unavailable ({e}), using MemorySaver.")
        return MemorySaver()


def _universal_route(state: BSBGraphState) -> str:
    """Universal router allowing any node to transition to any node."""
    if state.get("should_end"):
        return END
    
    next_a = state.get("next_agent", "__end__")
    if next_a == "__end__":
        return END
        
    return next_a


def build_graph(db, checkpoint_path: str = "./conversations_graph.db"):
    from app.agents.receptionist_agent import make_receptionist_agent
    from app.agents.sales_agent import make_sales_agent
    from app.agents.logistics_agent import make_logistics_agent
    from app.agents.nbfc_agent import make_nbfc_agent

    receptionist_fn = make_receptionist_agent(db)
    sales_fn = make_sales_agent(db)
    logistics_fn = make_logistics_agent(db)
    nbfc_fn = make_nbfc_agent(db)

    def _supervisor(state: BSBGraphState) -> dict[str, Any]:
        return supervisor_node(state, db=db)

    builder = StateGraph(BSBGraphState)

    builder.add_node("supervisor", _supervisor)
    builder.add_node("receptionist_agent", receptionist_fn)
    builder.add_node("sales_agent", sales_fn)
    builder.add_node("logistics_agent", logistics_fn)
    builder.add_node("nbfc_agent", nbfc_fn)

    builder.set_entry_point("supervisor")

    all_nodes = [
        "supervisor", 
        "receptionist_agent", 
        "sales_agent", 
        "logistics_agent", 
        "nbfc_agent", 
        END
    ]
    
    # Map identities for the dictionary required by add_conditional_edges
    path_map = {node: node for node in all_nodes}

    # Add dynamic routing from all agents
    for node in ["supervisor", "receptionist_agent", "sales_agent", "logistics_agent", "nbfc_agent"]:
        builder.add_conditional_edges(
            node,
            _universal_route,
            path_map,
        )

    checkpointer = _make_checkpointer(checkpoint_path)
    graph = builder.compile(checkpointer=checkpointer)
    logger.info("[BSBGraph] Multi-agent LangGraph compiled successfully.")
    return graph


_graph_cache: dict = {}

def get_graph(db, checkpoint_path: str = "./conversations_graph.db"):
    db_key = id(db)
    if db_key not in _graph_cache:
        _graph_cache[db_key] = build_graph(db, checkpoint_path)
    return _graph_cache[db_key]
