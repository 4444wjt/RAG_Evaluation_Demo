import importlib

mods = ['dotenv', 'langchain_community', 'langchain_text_splitters',
        'langchain_openai', 'ragas', 'datasets', 'sentence_transformers',
        'chromadb', 'pandas', 'numpy', 'matplotlib', 'gradio']
for m in mods:
    try:
        importlib.import_module(m)
        print(f"{m}: OK")
    except Exception as e:
        print(f"{m}: FAIL ({type(e).__name__}) {str(e)[:120]}")