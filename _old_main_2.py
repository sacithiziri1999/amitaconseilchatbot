import logging
import os
import time

import streamlit as st
from dotenv import load_dotenv
from langchain.prompts import ChatPromptTemplate, PromptTemplate
from langchain.retrievers.multi_query import MultiQueryRetriever
from langchain_community.vectorstores import FAISS
from langchain_chroma import Chroma
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.messages import AIMessage, HumanMessage
#import hashlib
from datetime import datetime

from load_pdf import LoadAndSplitDocuments

load_dotenv(dotenv_path='.config')


class InteractiveRAG:
    def __init__(self):
        os.environ["OPENAI_API_KEY"] = os.getenv('OPENAI_API_KEY', '')
        self.embedding_function = self.get_embedding_function()
        #self.update_vector_store_from_sharepoint()
        self._load_or_create_vector_db()
        self.llm = ChatOpenAI(
            model="gpt-3.5-turbo",
            temperature=0.7,
            max_tokens=None
        )
        self.QUERY_PROMPT = PromptTemplate(
            input_variables=["question"],
            template="""
                   Vous êtes un agent conversationnel pour les collaborateurs. 
                   Votre rôle est de les accompagner dans leur recherche d’informations en fournissant 
                   des réponses pertinentes et précises. Un contexte te sera fourni pour orienter ta réponse aux requêtes de l'utilisateur. 
                   Fournissez une réponse détaillée, complète et structurée. 
                   """
        )
        #"""
        #           Vous êtes un assistant intelligent francophone représentant . 
        #           Votre rôle est d’accompagner les collaborateurs dans leur recherche d’informations en fournissant 
        #           des réponses pertinentes et précises.
        #           Votre mission consiste à reformuler une seule fois la question posée par l’utilisateur afin d’optimiser 
        #           la récupération de documents pertinents à partir d’une base de données vectorielle, 
        #           tout en préservant l’intention initiale de la demande. Fournissez une réponse détaillée et complète.
        #           Question initiale : {question}
        #           """
        self.template_context_only = """Répondez à la question en vous appuyant uniquement sur le contexte suivant : {context}
            Question : {question}
            """
        
        self.template_mixed = """Répondez à la question en utilisant vos connaissances ainsi que le contexte suivant : {context}
            Question : {question}
            """
        self.retriever = MultiQueryRetriever.from_llm(
            self.db.as_retriever(search_kwargs={"k": 10}),
            self.llm,
            prompt=self.QUERY_PROMPT
        )  #

    def _load_or_create_vector_db(self):
        #vector_db_path = "./faiss_index"
        vector_db_path = "./chroma_index"

        if os.path.exists(vector_db_path):
            # Load existing vector store
            self.db = Chroma(persist_directory=vector_db_path, embedding_function=self.embedding_function)
            #self.db = FAISS.load_local(vector_db_path, self.embedding_function, allow_dangerous_deserialization=True)
        else:
            # Create and save vector store
            load_data = LoadAndSplitDocuments()
            document_chunks = load_data.run_load_and_split_documents()

            # Add sourcing metadata
            for doc in document_chunks:
                if "source" not in doc.metadata:
                    doc.metadata["source"] = "SharePoint"
                if "last_modified" not in doc.metadata:
                    doc.metadata["last_modified"] = datetime.now().isoformat().strftime("%Y-%m-%d %H:%M:%S")
                #doc.metadata["hash"] = hashlib.md5(doc.page_content.encode('utf-8')).hexdigest()
                
            # # Create vector store
            # self.db = FAISS.from_documents(document_chunks,
            #                                self.embedding_function)

            # # Persist vector store
            # self.db.save_local(vector_db_path)
     
            self.db = Chroma.from_documents(
            documents=document_chunks,
            embedding=self.embedding_function,
            persist_directory=vector_db_path
            )

    def update_vector_store_from_sharepoint(self): 

        directory = "./chroma_index"
        load_data = LoadAndSplitDocuments()
        document_chunks = load_data.run_load_and_split_documents()

        # Ajout métadonnées manquantes
        for doc in document_chunks:
            if "source" not in doc.metadata:
                doc.metadata["source"] = "SharePoint"
            if "last_modified" not in doc.metadata:
                doc.metadata["last_modified"] = datetime.now().isoformat().strftime("%Y-%m-%d %H:%M:%S")
            #doc.metadata["hash"] = hashlib.md5(doc.page_content.encode('utf-8')).hexdigest()

        if os.path.exists(directory) and len(os.listdir(directory)) > 0:
            logging.info("Chargement du vector store existant")
            self.db = Chroma(persist_directory=directory, embedding_function=self.embedding_function)
            # Récupérer les documents existants avec leurs métadonnées
            existing_docs = self.db._collection.get(include=["metadatas", "documents"])
            existing_docs_metadata = {
                meta["source"]: {"last_modified": meta["last_modified"], "hash": meta.get("hash", "")}
                for meta in existing_docs["metadatas"]
            }
            documents_to_add = []
            documents_to_update = []
            documents_to_remove = []

            #Documents à ajouter ou mettre à jour (date différente)
            for doc in document_chunks:
                source = doc.metadata["source"]            
                hash_value = doc.metadata["hash"]
                last_modified = doc.metadata["last_modified"]
                if source not in existing_docs_metadata:
                    documents_to_add.append(doc)
                elif existing_docs_metadata[source]["last_modified"] != last_modified:
                    documents_to_update.append(doc)

            # Documents à supprimer
            existing_sources = {meta["source"] for meta in existing_docs["metadatas"]}
            new_sources = {doc.metadata["source"] for doc in document_chunks}
            documents_to_remove = list(existing_sources - new_sources)

            # Mettre à jour le vector store
            logging.info(f"Ajout : {len(documents_to_add)}, Mise à jour : {len(documents_to_update)}, Suppression : {len(documents_to_remove)}")

            if documents_to_add or documents_to_update or documents_to_remove:

                # Suppression des documents obsolètes
                if documents_to_remove:
                    self.db._collection.delete(where={"source":{"$in":documents_to_remove}})
                    logging.info("Suppression")
                # Suppression ancienne version
                if documents_to_update:
                    self.db._collection.delete(where={"source": {"$in": [doc.metadata["source"] for doc in documents_to_update]}})

                # Ajout des nouveaux documents
                self.db.add_documents(documents_to_add + documents_to_update)
                logging.info("Ajout et mise à jour")
        else:
            logging.info("Création d'un nouveau vector store")
            # Créer un nouveau vector store
            self.db = Chroma.from_documents(
            documents=document_chunks,
            embedding=self.embedding_function,
            persist_directory=directory
            )
        #db.persist()
        logging.info(" Vector store enregistré localement avec succès !")

    def get_embedding_function(self):
        start_time = time.time()
        logging.info('get_embedding_function')
        embeddings = OpenAIEmbeddings(model="text-embedding-ada-002")
        end_time = time.time()
        logging.info(f'get_embedding_function done in {end_time - start_time}')
        return embeddings

    def run_rag_prompt(self, question: str,chat_history=None,use_model_knowledge=False):
        start_time = time.time()
        logging.info('Run run_rag_prompt')
        logging.info('1. prompt')

        history_text = "\n".join([f"Utilisateur: {msg['message']}" if msg['role'] == "user" else f"Assistant: {msg['message']}" for msg in chat_history[-3:]])
        logging.info(f"History : {history_text}")
        full_question = f"Contexte de la conversation :\n{history_text}\n\nNouvelle question : {question}\n\nFournissez une réponse détaillée et complète."
        template = self.template_mixed if use_model_knowledge else self.template_context_only
        prompt = ChatPromptTemplate.from_template(template)
        retrieved_docs = self.retriever.get_relevant_documents(full_question)
        sources = [doc.metadata.get("source", "Unknown") for doc in retrieved_docs]
        sources = set(sources)
        sources = list(sources)[:2]


        logging.info(f'1. prompt done {time.time() - start_time}')
        logging.info('2. chain')
        chain = (
                {"context": self.retriever, "question": RunnablePassthrough()}
                | prompt
                | self.llm
                | StrOutputParser()
        )

        logging.info(f'2. chain done {time.time() - start_time}')
        logging.info('3. result')
        result = chain.invoke(question)
  
        logging.info(f'3. result done {time.time() - start_time}')
        logging.info(f'run_rag_prompt done {time.time() - start_time}')
        return {"response": result, "resources": sources}

    def run_rag(self):
        while True:
            question = input("Welcome to GPT, comment puis-je vous aider ? 😊"
                             "(ou tapez 'exit' pour quitter ) : ")

            if question.lower() == "exit":
                print("Au revoir 👋!")
                break

            response = self.run_rag_prompt(question)
            print(f"Réponse : {response}")

    def main(self):
        # Display the logo at the top
        st.image("./image/img.png", width=200)
        # Initialize session state
        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []

        if "query_submitted" not in st.session_state:
            st.session_state.query_submitted = False

        if "question" not in st.session_state:
            st.session_state.question = ""

        if "messages" not in st.session_state:
            st.session_state.messages = [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there! How can I assist you today?"}
        ]
      
        with st.sidebar:
            st.header("Configuration")
            st.divider()
            st.selectbox(
            "🤖 Selectionner un Model", 
            ["OpenAI", "MistralAI"],
            key="model"
            )

            st.selectbox(
            "🗂️ Préciser la catégorie", 
            ["Tout","Business", "Interne","Essentials","Propale","CV","Formation"],
            key="category"
            )

            cols0 = st.columns(2)
            with cols0[0]:
                use_model_knowledge = st.toggle("Utiliser les connaissances du modèle (LLM + Vector Store)", value=False)
                # is_vector_db_loaded = ("vector_db" in st.session_state and st.session_state.vector_db is not None)
                # st.toggle(
                #     "Use RAG", 
                #     value=is_vector_db_loaded, 
                #     key="use_rag", 
                #     disabled=not is_vector_db_loaded,
                # )

            with cols0[1]:
                if st.button("Nouvelle conversation"):
                    st.session_state.chat_history = []
                    st.session_state.query_submitted = False
                    st.rerun() 
                #st.button("Clear Chat", on_click=lambda: st.session_state.messages.clear(), type="primary")

            st.header("RAG Sources:")
            
            # File upload input for RAG with documents
            st.file_uploader(
                "📄 Upload un document", 
                type=["pdf", "pptx", "docx", "xls"],
                accept_multiple_files=True,
                #on_change=load_doc_to_db,
                key="rag_docs",
            )

        # URL input for RAG with websites
            st.text_input(
                "🌐 Insérer une URL", 
                placeholder="https://url.com",
                #on_change=load_url_to_db,
                key="rag_url",
            )


        # Input field for user question
        question=st.text_input("Welcome to GPT! Comment puis-je t'aider ? 😊")

        use_model_knowledge = st.toggle("Utiliser les connaissances du modèle (LLM + Vector Store)", value=False)
        
        
        # When the "Réponse" button is clicked
        if st.button("Réponse"):
            st.session_state.query_submitted = True 

        # if prompt := st.chat_input("Your message"):
        #     st.session_state.messages.append({"role": "user", "content": prompt})
        #     with st.chat_message("user"):
        #         st.markdown(prompt)

        #     with st.chat_message("assistant"):
        #         message_placeholder = st.empty()
        #         full_response = ""

        #         messages = [HumanMessage(content=m["content"]) if m["role"] == "user" else AIMessage(content=m["content"]) for m in st.session_state.messages]

            # if not st.session_state.use_rag:
            #     st.write_stream(stream_llm_response(llm_stream, messages))
            # else:
            #     st.write_stream(stream_llm_rag_response(llm_stream, messages))
        # with st.chat_message("user"):
        #     st.markdown(prompt)
        if question.strip():
            if st.session_state.query_submitted or question != "":
                st.session_state.chat_history.append({"role": "user", "message": question})
                # Display a progress bar
                with st.spinner('Génération de la réponse...'):
                    progress_bar = st.progress(0)

                    # Simulate response generation process
                    for i in range(10):
                        time.sleep(0.1)  # Simulate time taken to generate response
                        progress_bar.progress((i + 1) * 10)

                    # Generate answer using the RAG system
                    result = self.run_rag_prompt(question=question,chat_history=st.session_state.chat_history,use_model_knowledge=use_model_knowledge)
                    answer = result["response"]
                    resources = result["resources"]

                    # Add assistant's answer to chat history
                    st.session_state.chat_history.append(
                        {"role": "assistant", "message": answer, "resources": resources})
                st.session_state.query_submitted = False

        # Display chat history in reverse order (latest first)
        st.write("### Conversation :")
        pass
        for chat in reversed(st.session_state.chat_history):
            if chat["role"] == "user":
                st.markdown(f"**Vous** : {chat['message']}")
            else:
                st.markdown(f"**GPT** : {chat['message']}")
                if "resources" in chat:
                    st.markdown("**Ressources** :")
                    for resource in chat['resources']:
                        st.markdown(f"- {resource}")


if __name__ == "__main__":
    rag = InteractiveRAG()
    rag.main()
