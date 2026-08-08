import spacy

nlp = spacy.load("en_core_web_sm")

doc = nlp("close all the open documents")

for token in doc:
    print(
        token.text,
        token.pos_,
        token.dep_,
        token.head.text,
    )