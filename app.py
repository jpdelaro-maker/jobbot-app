import streamlit as st
import pandas as pd
from job_engine.engine import run_search, build_regex_list

st.set_page_config(page_title="JobBot | Recherche d'offres", layout="wide")
st.title("🔎 JobBot — Recherche d'offres")

with st.sidebar:
    st.header("Critères")
    # On met tout dans un formulaire : à CHAQUE submit, ça relance la recherche avec les nouvelles valeurs
    with st.form("search_form", clear_on_submit=False):
        keywords = st.text_area(
            "Mots-clés (séparés par | )",
            value="Directeur commercial|Head of Sales|Sales Manager",
            height=110
        ).split("|")

        sites = st.multiselect(
            "Sites à interroger",
            ["apec", "indeed", "wttj", "hellowork"],
            default=["apec", "indeed", "wttj", "hellowork"],
            help="Décoche pour exclure un site"
        )

        min_score = st.slider("Score minimum", 0, 100, 40, step=5)
        cities = st.text_input("Lieu (bonus, séparés par | )", value="France|Télétravail|Remote").split("|")

        st.markdown("### Filtres avancés")
        must_have_txt = st.text_area("Must-have (OR, séparés par | )", value="")
        nice_to_have_txt = st.text_area("Nice-to-have (OR, séparés par | )", value="b2b|industrie|retail|RSE|IoT")
        exclude_txt = st.text_area("Exclusions (OR, séparés par | )", value="stage|alternance|junior|freelance")

        submitted = st.form_submit_button("Lancer la recherche ✅")

cfg = {
    "KEYWORDS": [k.strip() for k in keywords if k.strip()],
    "SITES": sites,
    "CITIES_BONUS": build_regex_list([c.strip().lower() for c in cities if c.strip()]),
    "MIN_SCORE": min_score,
    "MUST_HAVE": build_regex_list([x.strip() for x in must_have_txt.split("|") if x.strip()]) or build_regex_list([k.strip() for k in keywords if k.strip()]),
    "NICE_TO_HAVE": build_regex_list([x.strip() for x in nice_to_have_txt.split("|") if x.strip()]) or [],
    "EXCLUSIONS": build_regex_list([x.strip() for x in exclude_txt.split("|") if x.strip()]) or [],
    "SENIORITY": build_regex_list(["head","directeur","director","senior","lead","responsable","manager"]),
    "CONTRACT_PREFER": build_regex_list(["cdi","permanent"]),
    "CONTRACT_EXCLUDE": build_regex_list(["cdd","intérim","freelance","temporary","temp"]),
    "REMOTE_OK": build_regex_list(["télétravail","remote","hybride","hybrid"]),
    "COMPANY_WHITELIST": [],
    "COMPANY_BLACKLIST": [],
    "ALREADY_APPLIED_URLS": [],
}

col1, col2 = st.columns([2,1])

with col1:
    if submitted:
        with st.spinner("Recherche en cours..."):
            df, logs = run_search(cfg)
        st.subheader("Résultats")
        if df.empty:
            st.info("Aucun résultat avec ces critères.")
        else:
            st.dataframe(df, use_container_width=True)
            csv = df.to_csv(index=False).encode("utf-8")
            st.download_button("📥 Télécharger CSV", csv, "jobs.csv", "text/csv")

with col2:
    st.subheader("Logs")
    if submitted:
        st.code(logs or "(vide)")
    else:
        st.write("Remplis le formulaire puis clique **Lancer la recherche ✅**.")
