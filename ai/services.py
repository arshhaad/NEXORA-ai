from openai import OpenAI
import os


class AIService:
    def __init__(self):
        self.client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    def generate_response(self, prompt: str) -> str:
        if not self.client.api_key:
            return 'OpenAI API key is not configured.'
        response = self.client.responses.create(
            model='gpt-4o-mini',
            input=prompt,
        )
        return response.output_text
