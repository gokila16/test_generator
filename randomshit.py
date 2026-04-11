from google import genai

client = genai.Client(
    vertexai=True,
    project="project-4b6556ed-0771-4944-982",
    location="us-central1"
)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Say hello!"
)
print(response.text) 