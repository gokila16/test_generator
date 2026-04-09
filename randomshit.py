from src.context_loader import load_context_data, get_dependency_chain
import config

dep_chains, call_graph = load_context_data(
    config.DEPENDENCY_CHAINS_FILE, config.CALL_GRAPH_FILE
)

# Simulate both overloads
m1 = {"full_name": "org.apache.pdfbox.Loader.loadFDF",
      "signature": "public static FDFDocument loadFDF(File file) throws IOException"}
m2 = {"full_name": "org.apache.pdfbox.Loader.loadFDF",
      "signature": "public static FDFDocument loadFDF(InputStream input) throws IOException"}

print(get_dependency_chain(dep_chains, m1))
print(get_dependency_chain(dep_chains, m2))