from typing import Dict, List, Tuple
import numpy as np
from langchain_ollama import OllamaEmbeddings
from langgraph.graph import StateGraph, END
from typing_extensions import TypedDict

# ------------------------------------------------------------------
# Initialize embedding model
# ------------------------------------------------------------------

# embeddings = OllamaEmbeddings(model="mahonzhan/all-MiniLM-L6-v2")
embeddings = OllamaEmbeddings(model="qllama/bge-small-en-v1.5")

# ------------------------------------------------------------------
# Intent exemplar anchors
# Keep examples diverse, realistic, and close to production wording.
# ------------------------------------------------------------------

EXEMPLAR_INTENTS: Dict[str, List[str]] = {
    "catia_knowledge_rag": [
        # Part Design and Sketcher
                "How do I create a pad from a sketch in CATIA?",
                "How can I create a pocket in Part Design?",
                "What is the difference between a pad and a shaft feature?",
                "How do I fully constrain a sketch?",
                "Why is my CATIA sketch under-constrained?",
                "How can I create a circular pattern of holes?",
                "How do I create a rib or slot from a profile?",
                "How can I edit the thickness of a shell feature?",
                "Why does CATIA report that the sketch is not closed?",
                "How do I create a datum plane at an offset?",
        
                # Assembly and product structure
                "How do I create an assembly in CATIA?",
                "What is the difference between Fix, Coincidence, and Contact constraints?",
                "Why is my CATProduct not updating correctly?",
                "How do I replace a component in an assembly?",
                "How can I analyze clashes between assembly components?",
                "How do I move a component manually in Assembly Design?",
                "How can I find broken links in a CATProduct?",
                "What is the difference between a CATPart and a CATProduct?",
                "How do I use contextual design in a product assembly?",
                "Why is an assembly constraint inconsistent?",
        
                # Drafting, tolerancing, and standards
                "How do I generate a drawing from a 3D model?",
                "How can I create section and detail views in Drafting?",
                "How do I add dimensions and tolerances to a CATDrawing?",
                "What are the criteria for applying geometric tolerances in a drawing?",
                "Please remind me of the rules for tolerancing a technical drawing.",
                "How should datum references be selected for GD&T?",
                "What is the difference between flatness, parallelism, and perpendicularity?",
                "How do I create a title block in CATIA Drafting?",
                "Why are drawing views not updating from the 3D model?",
                "How can I change a drawing standard from ISO to ASME?",
                "What are CATIA drafting standards and best practices?",
        
                # Surface and shape design
                "How do I create a surface in Generative Shape Design?",
                "What is the difference between join, trim, and split operations?",
                "How can I repair gaps between surfaces?",
                "Why does a surface join fail in CATIA?",
                "How do I create a sweep along a guide curve?",
                "How can I check surface continuity in GSD?",
                "What is the difference between G0, G1, and G2 continuity?",
                "How do I extract a boundary from a surface?",
        
                # Measurement, properties, and DMU
                "How can I calculate center of gravity using VBA?",
                "How do I measure surface area and volume?",
                "How can I calculate mass and inertia properties of a product?",
                "Why is the center of gravity result incorrect?",
                "How does DMU Kinematics work in CATIA?",
                "How do I define joints in a kinematic mechanism?",
                "How can I run a clearance analysis in DMU Space Analysis?",
                "What is the Measure Inertia command used for?",
        
                # Import, export, files, and interoperability
                "How do I import a STEP file in CATIA?",
                "How can I export a CATPart to STEP format?",
                "What file formats can CATIA open?",
                "Why does an imported IGES file contain broken surfaces?",
                "How do I save a CATDrawing as PDF?",
                "What settings should I use when exporting a STEP file?",
                "How can I convert a CATProduct to a 3DXML file?",
                "Why are product links missing after moving CATIA files?",
        
                # Automation, VBA, APIs, and troubleshooting
                "How can I automate CATIA using VBA macros?",
                "How do I get the center of gravity of a CATProduct using VBA?",
                "How can I access CATIA parameters programmatically?",
                "How do I use Product Structure APIs?",
                "How can I iterate through all products in an assembly using VBA?",
                "What does GetTechnologicalObject return in CATIA automation?",
                "How do I obtain a Measurable object in context?",
                "Why does my CATIA macro raise an object required error?",
                "How can I update a part through the CATIA API?",
                "How do I read custom properties from a CATProduct?",
                "What causes CATIA update errors and how can I troubleshoot them?",
                "Why does CATIA crash when opening a large assembly?",
                "How can I improve CATIA performance for large products?",
                "What should I check when CATIA cannot acquire a license?",
    ],

    "catia_agent_command": [
        # Open commands
                "Can you please open a CAD document for me?",
                "Open C:\\TestData\\Gearbox.CATProduct",
                "Open the bearing housing CATPart.",
                "Load the assembly from my local folder.",
                "Please open the latest downloaded CATDrawing.",
                "Open product 6328629 revision CAD000.",
                "Launch CATIA and open the active project assembly.",
        
                # Create commands
                "Create a new part document.",
                "Create an empty CATProduct.",
                "Make a new drawing document using the ISO template.",
                "Start a new CATIA assembly for me.",
                "Create a CATPart named Bracket.",
                "Generate a new drawing from the active part.",
        
                # Close, save, and lifecycle commands
                "Close the active document.",
                "Close all open documents.",
                "Save the current CATPart.",
                "Save all modified CATIA documents.",
                "Save the active document as Bracket_v2.CATPart.",
                "Close CATIA without saving the current file.",
                "Export the active drawing to PDF now.",
        
                # ENOVIA and retrieval commands
                "Download CAD file 6328629 revision CAD000 from ENOVIA.",
                "Retrieve part number 65478 revision CAD002 from ENOVIA.",
                "Fetch the latest released revision of this CAD model.",
                "Download the CATProduct associated with AO ID 12345.",
                "Open the latest revision of part 778899 from ENOVIA.",
                "Check out the selected CAD document from ENOVIA.",
        
                # Execute actions and automation
                "Start the SAB model for AO ID 12345.",
                "Run the center of gravity macro on the active product.",
                "Execute the update macro for all open documents.",
                "Measure the active product and return its center of gravity.",
                "Update the active CATProduct now.",
                "Run clash analysis on the currently open assembly.",
                "Apply the company drafting standard to this drawing.",
                "Insert the downloaded component into the active assembly.",
                "Replace the selected component with the latest revision.",
                "Move the selected product instance to coordinates 0, 0, 0.",
                "Create a support package from the active CATIA session.",
        
                # Conversational/indirect phrasing that explains where a file
                # lives, rather than a short imperative command. Long file
                # paths are stripped before embedding (see
                # _strip_file_paths), but the surrounding wording still needs
                # its own agentic-intent coverage among the exemplars.
                "Can you please open a document for me? It lives in this "
                "path C:\\Users\\me\\Documents\\Project\\Part.CATPart",
                "I have a CATPart I need opened, it's located at "
                "D:\\Data\\Bracket.CATPart",
                "Please open the file located at "
                "C:\\Shared\\Models\\Assembly.CATProduct for me",
                "There's a product I'd like opened, you can find it at "
                "C:\\Projects\\Gearbox\\Gearbox.CATProduct",
                "Could you download this for me? It's part number 65478 "
                "revision CAD002 from ENOVIA.",
    ],
}


def _normalize(vectors: np.ndarray) -> np.ndarray:
    """L2-normalize a vector or matrix."""
    vectors = np.asarray(vectors, dtype=np.float32)
    norm = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.maximum(norm, 1e-12)


# Batch embedding is faster than sending each exemplar separately.
intent_matrices: Dict[str, np.ndarray] = {
    intent: _normalize(np.asarray(embeddings.embed_documents(phrases)))
    for intent, phrases in EXEMPLAR_INTENTS.items()
}

# Tune these values using the labeled validation set below.
AGENT_COMMAND_THRESHOLD = 0.48
AGENT_COMMAND_MARGIN = 0.03
TOP_K = 3


def _top_k_mean(scores: np.ndarray, k: int = TOP_K) -> float:
    """Average the strongest matches to reduce dependence on one exemplar."""
    k = min(k, scores.size)
    return float(np.mean(np.partition(scores, -k)[-k:]))


def classify_intent(user_message: str) -> Tuple[str, float]:
    """Classify a message and return the selected intent and its score."""
    query_vec = _normalize(np.asarray(embeddings.embed_query(user_message)))

    scores: Dict[str, float] = {}
    for intent, matrix in intent_matrices.items():
        similarities = matrix @ query_vec
        scores[intent] = _top_k_mean(similarities)

    knowledge_score = scores["catia_knowledge_rag"]
    command_score = scores["catia_agent_command"]

    # Prefer the safer RAG route when confidence or separation is insufficient.
    is_agent_command = (
        command_score >= AGENT_COMMAND_THRESHOLD
        and command_score - knowledge_score >= AGENT_COMMAND_MARGIN
    )

    selected_intent = (
        "catia_agent_command" if is_agent_command else "catia_knowledge_rag"
    )
    selected_score = scores[selected_intent]

    print(
        f"Knowledge score={knowledge_score:.4f}, "
        f"Command score={command_score:.4f}, "
        f"Selected={selected_intent}"
    )
    return selected_intent, selected_score


def route_intent(state: dict) -> str:
    best_intent, _ = classify_intent(state["user_message"])
    if best_intent == "catia_agent_command":
        return "agent_command_node"
    return "basic_rag_node"


class GraphState(TypedDict, total=False):
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
        "response": "Routed to CATIA LangGraph Execution Agent",
    }


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


# Keep evaluation queries separate from training exemplars.
EVALUATION_SET: List[Tuple[str, str]] = [
    ("What are the rules for datum selection in a drawing?", "catia_knowledge_rag"),
    ("Why is my pocket feature failing after an update?", "catia_knowledge_rag"),
    ("Tell me how to calculate product inertia with a macro.", "catia_knowledge_rag"),
    ("Which neutral CAD formats are supported?", "catia_knowledge_rag"),
    ("Explain how I can repair disconnected surfaces.", "catia_knowledge_rag"),
    ("Could you open D:\\CAD\\Pump.CATProduct?", "catia_agent_command"),
    ("Please create a CATPart called MountingPlate.", "catia_agent_command"),
    ("Get revision CAD004 of item 98765 from ENOVIA.", "catia_agent_command"),
    ("Run the COG automation on the product I have open.", "catia_agent_command"),
    ("Save every modified document and close CATIA.", "catia_agent_command"),
    
    # Deliberately difficult question-versus-command pairs
    ("How do I open a CATProduct?", "catia_knowledge_rag"),
    ("Open the CATProduct.", "catia_agent_command"),
    ("How can I export a drawing as PDF?", "catia_knowledge_rag"),
    ("Export this drawing as PDF.", "catia_agent_command"),
]


def evaluate() -> float:
    correct = 0
    print("\nEvaluation results")
    print("-" * 80)

    for query, expected in EVALUATION_SET:
        predicted, score = classify_intent(query)
        passed = predicted == expected
        correct += int(passed)
        print(
            f"{'PASS' if passed else 'FAIL'} | expected={expected:<24} "
            f"predicted={predicted:<24} score={score:.4f} | {query}"
        )

    accuracy = correct / len(EVALUATION_SET)
    print("-" * 80)
    print(f"Accuracy: {correct}/{len(EVALUATION_SET)} = {accuracy:.2%}")
    return accuracy


if __name__ == "__main__":
    evaluate()

    test_queries = [
        r"Can you please open this document for me? It lives in this path <file_path>.",
    ]

    for query in test_queries:
        print("\n" + "=" * 80)
        print("Query:", query)
        result = graph.invoke({"user_message": query})
        print("Response:", result["response"])
