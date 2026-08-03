from catia_agent.graph import catia_agent_graph


print(
    catia_agent_graph
    .get_graph()
    .draw_mermaid()
)