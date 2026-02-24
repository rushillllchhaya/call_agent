from app.services.ai_agent import llm as base_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# Create a non-JSON forced LLM for simple classification
intent_llm = base_llm.copy(update={"model_kwargs": {}})

class IntentRouter:
    @staticmethod
    def classify_intent(text: str) -> str:
        prompt = f"""
        Classify the user's intent into exactly ONE of the following categories:

        1. BUY_FLOW: User wants to buy something.
        2. SELL_FLOW: User wants to sell something.
        3. ASK_PRODUCTS: User asks "what do you sell?", "products?", "inventory?".
        4. ASK_GRADES: User specifically asks about "grades" or "specs".
        5. ASK_PRICE: User asks "what is the price?", "market rate?", "how much?".
        6. MOVE_FORWARD: User says "next", "move on", "aage badhte hain", "continue".
        7. UNSUPPORTED_PRODUCT: User asks for Nylon, ABS, or non-polymers.
           (Supported: HDPE, LDPE, PP, PET, PVC)
        8. HUMAN_REQUEST: User asks for a human/agent/operator.
        9. GENERAL_QUESTION: General chit-chat or off-topic.

        User Text: "{text}"

        Return ONLY the Category Name (e.g., BUY_FLOW). Do not add punctuation.
        """
        
        try:
            chain = ChatPromptTemplate.from_template(prompt) | intent_llm | StrOutputParser()
            intent = chain.invoke({})
            return intent.strip().upper()
        except Exception as e:
            print(f"ERROR in Intent Classification: {e}")
            return "GENERAL_QUESTION"
