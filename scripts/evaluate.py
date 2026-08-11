import os
import sys
import json
import asyncio
from pathlib import Path

# Add backend directory to sys.path
backend_path = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(backend_path))

from app.agents.graph import run_support_agent

def run_evaluation():
    eval_file = Path(__file__).resolve().parent.parent / "evaluation" / "questions.json"
    if not eval_file.exists():
        print(f"Error: evaluation file not found at {eval_file}")
        sys.exit(1)

    with open(eval_file, "r", encoding="utf-8") as f:
        questions = json.load(f)

    print("=" * 70)
    print(f"AI Customer Support System - Benchmark Evaluation ({len(questions)} Questions)")
    print("=" * 70)

    total = len(questions)
    correct_intents = 0
    correct_escalations = 0
    correct_tools = 0
    tool_cases = 0
    citation_count = 0
    rag_cases = 0

    results_table = []

    import time
    for item in questions:
        q_id = item["id"]
        query = item["question"]
        time.sleep(0.5)
        expected_intent = item["expected_intent"]
        expected_escalate = item["should_escalate"]
        expected_tool = item.get("expected_tool")

        # Run agent graph
        state = run_support_agent(
            session_id=f"eval_session_{q_id}",
            user_id="eval_user",
            user_message=query
        )

        detected_intent = state.get("intent", "").upper()
        escalated = state.get("escalation_required", False)
        tool_used = state.get("tool_name")
        sources = state.get("sources", [])
        response = state.get("response", "")

        intent_match = (detected_intent == expected_intent)
        escalation_match = (escalated == expected_escalate)
        
        if intent_match:
            correct_intents += 1
        if escalation_match:
            correct_escalations += 1

        tool_match = None
        if expected_tool:
            tool_cases += 1
            if tool_used == expected_tool:
                correct_tools += 1
                tool_match = True
            else:
                tool_match = False

        if expected_intent in ["FAQ", "PRODUCT", "PRICING", "REFUND", "SHIPPING", "TROUBLESHOOTING"]:
            rag_cases += 1
            if len(sources) > 0 or "Source" in response:
                citation_count += 1

        results_table.append({
            "id": q_id,
            "question": query[:40] + "..." if len(query) > 40 else query,
            "expected_intent": expected_intent,
            "detected_intent": detected_intent,
            "intent_pass": "✅" if intent_match else "❌",
            "escalated": "YES" if escalated else "NO",
            "escalate_pass": "✅" if escalation_match else "❌"
        })

    print(f"\n{'ID':<4} | {'Query':<43} | {'Expected Intent':<16} | {'Detected':<16} | {'Intent':<6} | {'Escalate':<8}")
    print("-" * 105)
    for r in results_table:
        print(f"{r['id']:<4} | {r['question']:<43} | {r['expected_intent']:<16} | {r['detected_intent']:<16} | {r['intent_pass']:<6} | {r['escalate_pass']:<8}")

    print("\n" + "=" * 70)
    print("EVALUATION METRICS SUMMARY")
    print("=" * 70)
    intent_acc = (correct_intents / total) * 100
    escalate_acc = (correct_escalations / total) * 100
    tool_acc = (correct_tools / tool_cases * 100) if tool_cases > 0 else 100.0
    citation_acc = (citation_count / rag_cases * 100) if rag_cases > 0 else 100.0

    print(f"Total Benchmark Queries Analyzed  : {total}")
    print(f"Intent Classification Accuracy    : {intent_acc:.1f}% ({correct_intents}/{total})")
    print(f"Escalation Decision Accuracy      : {escalate_acc:.1f}% ({correct_escalations}/{total})")
    print(f"Tool Selection Accuracy           : {tool_acc:.1f}% ({correct_tools}/{tool_cases})")
    print(f"RAG Citation Retrieval Coverage   : {citation_acc:.1f}% ({citation_count}/{rag_cases})")
    print("=" * 70)

if __name__ == "__main__":
    run_evaluation()
