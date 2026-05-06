import streamlit as st

import business_taxonomy_app as app
from compliance_recall_controller import ComplianceRecallController


@st.cache_resource
def get_model_recall_controller(model_name):
    return ComplianceRecallController(
        model=model_name,
        recall_judgement_mode="llm",
        atom_analysis_mode="llm",
        final_judgement_mode="llm",
    )


app.get_recall_controller = get_model_recall_controller


if __name__ == "__main__":
    app.main()
