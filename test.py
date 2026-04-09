from google import genai

client = genai.Client(api_key="AIzaSyA8dXub2zXHV1S1AoxkndjVHdbwCXB27Ig")

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="Say hello!"
)
print(response.text)