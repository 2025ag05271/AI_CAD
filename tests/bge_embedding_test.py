# import numpy as np
# from langchain_community.utils.math import cosine_similarity
# from langchain_ollama import OllamaEmbeddings
# from langgraph.graph import StateGraph, END
# from typing_extensions import TypedDict

# # 1. Initialize Ollama Embeddings
# embeddings = OllamaEmbeddings(
#     model="qllama/bge-small-en-v1.5"
# )

# # 2. CATIA Support Examples
# CATIA_EXAMPLES = [
#     "How do I create a new part in CATIA?",
#     "How can I generate a drawing from a 3D model?",
#     "How do I create an assembly in CATIA?",
#     "What is the difference between Part Design and Generative Shape Design?",
#     "How do I use Sketcher workbench?",
#     "How can I measure the distance between two points in CATIA?",
#     "How do I create a pad from a sketch?",
#     "How can I import a STEP file into CATIA?",
#     "How do I export a CATPart to STEP format?",
#     "Why is my CATIA assembly not updating?",
#     "How do I use constraints in Assembly Design?",
#     "How can I calculate the center of gravity of a product?",
#     "What is a CATProduct file?",
#     "How do I create a surface in Generative Shape Design?",
#     "How can I automate CATIA using VBA macros?",
#     "How do I use Product Structure APIs?",
#     "How can I access CATIA parameters programmatically?",
#     "How do I move a component within an assembly?",
#     "Why am I getting update errors in CATIA?",
#     "How do I work with DMU Navigator?"
# ]

# # Human Agent Examples
# AGENT_EXAMPLES = [
#     "I need a CATIA expert to help me.",
#     "Please connect me with a CATIA support engineer.",
#     "I want to speak with a CATIA specialist.",
#     "This CATIA issue requires human assistance.",
#     "Can you escalate this CATIA problem to support?",
#     "I need help from the CATIA automation team.",
#     "Please create a support ticket for this CATIA error.",
#     "Connect me to a CATIA administrator.",
#     "I am stuck with a CATIA macro issue and need an engineer.",
#     "Transfer me to the CATIA support team.",
#     "I need assistance troubleshooting CATIA installation problems.",
#     "Please escalate my CATIA licensing issue.",
#     "I cannot resolve this CATIA assembly error, connect me to support.",
#     "I need an expert to investigate this CATIA crash.",
#     "Can I speak with someone from the CATIA technical support team?"
# ]

# # Pre-compute embeddings
# catia_vectors = embeddings.embed_documents(CATIA_EXAMPLES)
# agent_vectors = embeddings.embed_documents(AGENT_EXAMPLES)

# SIMILARITY_THRESHOLD = 0.55


# # 3. Router Function
# def route_intent(state: dict) -> str:
#     user_message = state["user_message"]

#     query_vector = embeddings.embed_query(user_message)

#     catia_scores = cosine_similarity(
#         [query_vector],
#         catia_vectors
#     )[0]

#     agent_scores = cosine_similarity(
#         [query_vector],
#         agent_vectors
#     )[0]

#     max_catia = np.max(catia_scores)
#     max_agent = np.max(agent_scores)

#     print(f"CATIA Score: {max_catia:.4f}")
#     print(f"Agent Score: {max_agent:.4f}")

#     # Explicit human handoff
#     if max_agent > max_catia and max_agent > SIMILARITY_THRESHOLD:
#         return "agent_node"

#     return "basic_node"


# # 4. Define State
# class GraphState(TypedDict):
#     user_message: str
#     response: str


# # 5. Nodes
# def handle_basic(state: GraphState):
#     return {
#         "response": "Route to CATIA Knowledge Base / RAG Pipeline"
#     }


# def handle_agent(state: GraphState):
#     return {
#         "response": "Route to Human Support Queue"
#     }


# # 6. Build Graph
# builder = StateGraph(GraphState)

# builder.add_node("basic_node", handle_basic)
# builder.add_node("agent_node", handle_agent)

# builder.set_conditional_entry_point(
#     route_intent,
#     {
#         "basic_node": "basic_node",
#         "agent_node": "agent_node"
#     }
# )

# builder.add_edge("basic_node", END)
# builder.add_edge("agent_node", END)

# graph = builder.compile()


# # 7. Examples
# test_queries = [
#     "How do I create a pad in CATIA?",
#     "How can I import a STEP file?",
#     "Please connect me to a support engineer.",
#     "Can you please open a cad document for me?",
#     "How do I get the COG of a CATProduct using VBA?"
# ]

# for query in test_queries:
#     result = graph.invoke({"user_message": query})
#     print(f"\nQuery: {query}")
#     print(f"Response: {result['response']}")

# import numpy as np
# from langchain_community.utils.math import cosine_similarity
# from langchain_ollama import OllamaEmbeddings
# from langgraph.graph import StateGraph, END
# from typing_extensions import TypedDict

# # Initialize BGE Embedding Model
# embeddings = OllamaEmbeddings(
#     model="qllama/bge-small-en-v1.5"
# )

# # ------------------------------------------------------------------
# # Intent Descriptions
# # ------------------------------------------------------------------

# INTENTS = {
#     "catia_knowledge": """
#     Questions related to CATIA usage, part design, assembly design,
#     drafting, generative shape design, DMU, modeling, constraints,
#     measurements, product structure, CATPart, CATProduct,
#     troubleshooting, automation, VBA macros, APIs, parameters,
#     center of gravity calculation, CAD operations, import/export,
#     STEP files and general CATIA support.
#     """,

#     "agent_escalation": """
#     The user wants help from a human expert, CATIA support engineer,
#     CATIA specialist, automation engineer, technical support team,
#     issue escalation, ticket creation, direct assistance, live help
#     or expert intervention.
#     """
# }

# # Precompute Intent Embeddings
# intent_vectors = {
#     intent: embeddings.embed_query(description)
#     for intent, description in INTENTS.items()
# }

# SIMILARITY_THRESHOLD = 0.45


# # ------------------------------------------------------------------
# # Router
# # ------------------------------------------------------------------

# def route_intent(state: dict) -> str:

#     user_message = state["user_message"]

#     query_vector = embeddings.embed_query(user_message)

#     scores = {}

#     for intent, vector in intent_vectors.items():

#         score = cosine_similarity(
#             [query_vector],
#             [vector]
#         )[0][0]

#         scores[intent] = score

#     best_intent = max(scores, key=scores.get)
#     best_score = scores[best_intent]

#     print("\nIntent Scores:")
#     for intent, score in scores.items():
#         print(f"{intent}: {score:.4f}")

#     print(f"Selected: {best_intent} ({best_score:.4f})")

#     if (
#         best_intent == "agent_escalation"
#         and best_score > SIMILARITY_THRESHOLD
#     ):
#         return "agent_node"

#     return "basic_node"


# # ------------------------------------------------------------------
# # Graph State
# # ------------------------------------------------------------------

# class GraphState(TypedDict):
#     user_message: str
#     response: str


# # ------------------------------------------------------------------
# # Nodes
# # ------------------------------------------------------------------

# def handle_basic(state: GraphState):
#     return {
#         "response": "Route to CATIA Knowledge Base / RAG System"
#     }


# def handle_agent(state: GraphState):
#     return {
#         "response": "Route to CATIA Support Engineer"
#     }


# # ------------------------------------------------------------------
# # Graph
# # ------------------------------------------------------------------

# builder = StateGraph(GraphState)

# builder.add_node("basic_node", handle_basic)
# builder.add_node("agent_node", handle_agent)

# builder.set_conditional_entry_point(
#     route_intent,
#     {
#         "basic_node": "basic_node",
#         "agent_node": "agent_node"
#     }
# )

# builder.add_edge("basic_node", END)
# builder.add_edge("agent_node", END)

# graph = builder.compile()


# # ------------------------------------------------------------------
# # Test Queries
# # ------------------------------------------------------------------

# test_queries = [
#     "How do I create a pad from a sketch in CATIA?",
#     "How can I calculate center of gravity using VBA?",
#     "Can you please open a cad document for me?",
#     "How do I import a STEP file?",
#     "Please connect me with a CATIA expert.",
#     "I need help from a support engineer.",
#     "Escalate this issue to the CATIA team.",
# ]

# for query in test_queries:

#     print("\n" + "=" * 60)
#     print("Query:", query)

#     result = graph.invoke({
#         "user_message": query
#     })

#     print("Response:", result["response"])


from typing import Dict, List, Tuple
import numpy as np
from langchain_ollama import OllamaEmbeddings
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

# ------------------------------------------------------------------
# Initialize BGE Embedding Model
# ------------------------------------------------------------------
embeddings = OllamaEmbeddings(
    # model="qllama/bge-small-en-v1.5"
    model="mahonzhan/all-MiniLM-L6-v2"
)

# ------------------------------------------------------------------
# Intent Exemplar Anchors
# ------------------------------------------------------------------
EXEMPLAR_INTENTS: Dict[str, List[str]] = {
    "catia_knowledge_rag": [
        "How do I create a pad from a sketch in CATIA?",
        "How can I calculate center of gravity using VBA?",
        "How do I import a STEP file in CATIA?",
        "What is the shortcut for drafting view creation?",
        "Explain how DMU kinematics works in CATIA.",
        "How to measure surface area and volume?",
        "Troubleshooting drafting parameters and constraints.",
        "What file formats can CATIA open?",
    ],
    "catia_agent_command": [
        "Can you please open a cad document for me?",
        "Open C:\\TestData\\Gearbox.CATProduct",
        "Create a new part document",
        "Download CAD file 6328629 revision CAD000 from Enovia",
        "Close the active document",
        "Start the SAB model for AO ID 12345",
        "Close all open documents",
    ],
}

# ------------------------------------------------------------------
# Precompute & L2-Normalize Exemplar Vectors
# ------------------------------------------------------------------
def _normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(norm, 1e-12)

intent_matrices: Dict[str, np.ndarray] = {}

for intent, phrases in EXEMPLAR_INTENTS.items():
    raw_vectors = np.array([embeddings.embed_query(p) for p in phrases])
    intent_matrices[intent] = _normalize(raw_vectors)

# Similarity threshold to confirm Agent command intent
AGENT_COMMAND_THRESHOLD = 0.42

# ------------------------------------------------------------------
# Router Decision Logic
# ------------------------------------------------------------------
def classify_intent(user_message: str) -> Tuple[str, float]:
    query_vec = _normalize(np.array(embeddings.embed_query(user_message)))
    
    scores: Dict[str, float] = {}
    for intent, matrix in intent_matrices.items():
        sims = np.dot(matrix, query_vec)
        scores[intent] = float(np.max(sims))
    
    best_intent = max(scores, key=scores.get)
    best_score = scores[best_intent]
    
    print(f"\n[Router Logs]")
    for intent, score in scores.items():
        print(f"  - {intent}: {score:.4f}")
    print(f"Selected: {best_intent} ({best_score:.4f})")
    
    return best_intent, best_score


def route_intent(state: dict) -> str:
    user_message = state["user_message"]
    best_intent, best_score = classify_intent(user_message)
    
    if best_intent == "catia_agent_command" and best_score >= AGENT_COMMAND_THRESHOLD:
        return "agent_command_node"
    
    # Default fallback to RAG for informational queries
    return "basic_rag_node"

# ------------------------------------------------------------------
# Graph State & Nodes
# ------------------------------------------------------------------
class GraphState(TypedDict):
    user_message: str
    selected_node: str
    response: str


def handle_basic_rag(state: GraphState) -> dict:
    return {
        "selected_node": "basic_rag_node",
        "response": "Routed to CATIA Knowledge Base / RAG Endpoint",
    }


def handle_agent_command(state: GraphState) -> dict:
    return {
        "selected_node": "agent_command_node",
        "response": "Routed to CATIA LangGraph Execution Agent.",
    }

# ------------------------------------------------------------------
# Graph Construction
# ------------------------------------------------------------------
builder = StateGraph(GraphState)

builder.add_node("basic_rag_node", handle_basic_rag)
builder.add_node("agent_command_node", handle_agent_command)

builder.set_conditional_entry_point(
    route_intent,
    {
        "basic_rag_node": "basic_rag_node",
        "agent_command_node": "agent_command_node",
    },
)

builder.add_edge("basic_rag_node", END)
builder.add_edge("agent_command_node", END)

graph = builder.compile()

# ------------------------------------------------------------------
# Test Execution
# ------------------------------------------------------------------
if __name__ == "__main__":
    test_queries = [
        # "How do I create a pad from a sketch in CATIA?",
        # "How can I calculate center of gravity using VBA?",
        # "Can you please open a cad document for me?",
        # "How do I import a STEP file?",
        # "Download CAD file 65478 CAD002 from Enovia",
        # "Create a new part document",
        # "An information about CAD Standards and best practices in CATIA is needed.",
        "i got my facts about tolerancing in the drawing wrong, could you please remind me the criterias?",
    ]

    for query in test_queries:
        print("\n" + "=" * 65)
        print("Query:", query)
        result = graph.invoke({"user_message": query})
        print("Destination Node:", result["selected_node"])
        print("Action Taken:", result["response"])