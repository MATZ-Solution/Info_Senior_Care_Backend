import os
import typesense
from dotenv import load_dotenv

load_dotenv()

TS_CLIENT = None
try:
    TS_CLIENT = typesense.Client({
        'nodes': [{
            'host': "f4roslw27mz183yhp-1.a1.typesense.net",  # For Typesense Cloud use xxx.a1.typesense.net
            'port': 443,       # For Typesense Cloud use 443
            'protocol': "https"    # For Typesense Cloud use https
        }],
        'api_key': "Uc2eblzjXkLrtXb9w40y8OqrSbmIvhoh",
        'connection_timeout_seconds': 10
    })
except Exception as e:
    raise ValueError(f"Some error occured while initializing TypeSense, Error: {e}")


def retrieve_collection_details(collection_name: str):
    try:
        print("Retrieving collection details...")

        collection_details = TS_CLIENT.collections[collection_name].retrieve()

        print(f"{'='*50}\n{collection_details}\n{'='*50}")
    except Exception as e:
        print.info(f"Collection retrieval failed, Error: {e}")



retrieve_collection_details("facilities")