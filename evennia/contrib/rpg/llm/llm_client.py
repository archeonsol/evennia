"""
LLM (Large Language Model) client, for communicating with an LLM backend. This can be used
for generating texts for AI npcs, or for fine-tuning the LLM on a given prompt.

Note that running a LLM locally requires a lot of power, and ideally a powerful GPU. Testing
this with CPU mode on a beefy laptop, still takes some 4s just on a very small model.

The server defaults to output suitable for a local server
https://github.com/oobabooga/text-generation-webui, but could be used for other LLM servers too.

See the LLM instructions on that page for how to set up the server. You'll also need
a model file - there are thousands to try out on https://huggingface.co/models (you want Text
Generation models specifically).

# Optional Evennia settings (if not given, these defaults are used)

DEFAULT_LLM_HOST = "http://localhost:5000"
DEFAULT_LLM_PATH = "/api/v1/generate"
DEFAULT_LLM_HEADERS = {"Content-Type": "application/json"}
DEFAULT_LLM_PROMPT_KEYNAME = "prompt"
DEFAULT_LLM_REQUEST_BODY = {...}   # see below, this controls how to prompt the LLM server.

"""

import json

from django.conf import settings
from evennia import logger
from evennia.utils import http
from evennia.utils.utils import make_iter

DEFAULT_LLM_HOST = "http://127.0.0.1:5000"
DEFAULT_LLM_PATH = "/api/v1/generate"
DEFAULT_LLM_HEADERS = {"Content-Type": ["application/json"]}
DEFAULT_LLM_PROMPT_KEYNAME = "prompt"
DEFAULT_LLM_API_TYPE = ""  # or openai
DEFAULT_LLM_REQUEST_BODY = {
    "max_new_tokens": 250,  # max number of tokens to generate
    "temperature": 0.7,  # higher = more random, lower = more predictable
}


class LLMClient:
    """
    A client for communicating with an LLM server.

    """

    def __init__(self, on_bad_request=None):
        self.prompt_keyname = getattr(settings, "LLM_PROMPT_KEYNAME", DEFAULT_LLM_PROMPT_KEYNAME)
        self.hostname = getattr(settings, "LLM_HOST", DEFAULT_LLM_HOST)
        self.pathname = getattr(settings, "LLM_PATH", DEFAULT_LLM_PATH)
        self.headers = getattr(settings, "LLM_HEADERS", DEFAULT_LLM_HEADERS)
        self.request_body = getattr(settings, "LLM_REQUEST_BODY", DEFAULT_LLM_REQUEST_BODY)

        self.api_type = getattr(settings, "LLM_API_TYPE", DEFAULT_LLM_API_TYPE)

    def _format_request_body(self, prompt):
        """Structure the request body for the LLM server"""
        request_body = self.request_body.copy()

        prompt = "\n".join(make_iter(prompt))

        request_body[self.prompt_keyname] = prompt

        return request_body

    def _handle_llm_error(self, failure):
        """Correctly handle server connection errors"""
        failure.trap(Exception)
        return (500, failure.getErrorMessage())

    def _get_response_from_llm_server(self, prompt):
        """Call the LLM server and handle the response/failure"""
        request_body = self._format_request_body(prompt)

        if settings.DEBUG:
            logger.log_info(f"LLM request body: {request_body}")

        return http.request(
            "POST",
            self.hostname + self.pathname,
            headers=self.headers,
            data=json.dumps(request_body),
        ).addCallbacks(
            lambda response: (response.code, response.content),
            self._handle_llm_error,
        )

    async def get_response(self, prompt):
        """
        Get a response from the LLM server for the given npc.

        Args:
            prompt (str or list): The prompt to send to the LLM server. If a list,
                this is assumed to be the chat history so far, and will be added to the
                prompt in a way suitable for the api.

        Returns:
            str: The generated text response. Will return an empty string
                if there is an issue with the server, in which case the
                the caller is expected to handle this gracefully.

        """
        status_code, response = await self._get_response_from_llm_server(prompt)
        if status_code == 200:
            if settings.DEBUG:
                logger.log_info(f"LLM response: {response}")
            return json.loads(response)["results"][0]["text"]
        else:
            logger.log_err(f"LLM API error (status {status_code}): {response}")
            return ""
