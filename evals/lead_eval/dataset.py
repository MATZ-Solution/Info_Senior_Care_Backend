from evals.langsmith_client import get_langsmith_client

examples = [
    # greetings / small talk -- a warm, brief reply is correct; jumping
    # straight into Phase 1 or asking for details on a bare greeting is not
    {
        "inputs": {
            "question": "Hi",
            "history": [],
        },
    },
    {
        "inputs": {
            "question": "Hello, how are you?",
            "history": [],
        },
    },
    {
        "inputs": {
            "question": "Good morning!",
            "history": [],
        },
    },
    {
        "inputs": {
            "question": "What can you help me with?",
            "history": [],
        },
    },
    {
        "inputs": {
            "question": "Thanks so much, have a great day!",
            "history": [],
        },
    },

    # Phase 1 -- fresh emotional opener, no prior context. Must acknowledge
    # the feeling and normalize it before anything else -- no tool call, no
    # detail questions yet.
    {
        "inputs": {
            "question": "My mother has been very lonely since my father passed.",
            "history": [],
        },
    },

    # user explicitly asks to skip ahead to an advisor -- may honor the
    # request more directly than usual, but still shouldn't be cold/form-like
    # or demand info the user hasn't offered.
    {
        "inputs": {
            "question": "Can you just connect me with an advisor directly?",
            "history": [
                {"role": "user", "content": "Looking for a nursing home for my dad."},
            ],
        },
    },

    # Phase 1 -> Phase 2/3 transition: empathy already given, user just
    # acknowledged it. Next reply should share ONE expert insight then ask
    # permission -- must NOT skip straight to asking for location/contact.
    {
        "inputs": {
            "question": "Yeah, it's been on my mind a lot.",
            "history": [
                {"role": "user", "content": "My dad fell twice this week, I'm really worried."},
                {"role": "assistant", "content": "I'm so sorry -- that's stressful. Falls like these are actually one of the most common signs families notice when a loved one starts needing more support."},
            ],
        },
    },

    # Phase 3 -> Phase 4 transition: permission just granted. Next reply
    # should ask ONE detail (location), explain why, and must NOT ask for
    # contact info yet -- that needs a separate, later permission.
    {
        "inputs": {
            "question": "Yes, please.",
            "history": [
                {"role": "user", "content": "My dad fell twice this week, I'm really worried."},
                {"role": "assistant", "content": "I'm so sorry -- that's stressful. Falls like these are actually one of the most common signs families notice when a loved one starts needing more support. Having professional supervision available, even part-time, can make a real difference -- assisted living gives round-the-clock staff so no fall goes unnoticed. Would you like me to explore some options near you?"},
            ],
        },
    },

    # mid-Phase-4, anti-interrogation: location and age already collected.
    # Next reply must ask ONLY the next new detail (living situation), not
    # re-ask location/age, and must explain why it's asking.
    {
        "inputs": {
            "question": "He's 82.",
            "history": [
                {"role": "user", "content": "My dad fell twice this week, I'm really worried."},
                {"role": "assistant", "content": "I'm so sorry -- that's stressful. Falls like these are one of the most common signs families notice when a loved one needs more support. Would you like me to explore some options near you?"},
                {"role": "user", "content": "Yes, please."},
                {"role": "assistant", "content": "To find the closest options for you -- what city or ZIP code are you in?"},
                {"role": "user", "content": "We're in Tucson, AZ."},
                {"role": "assistant", "content": "Got it, thank you. Roughly how old is your dad? That helps me match the right level of care."},
            ],
        },
    },

    # location correction mid-flow: reply should acknowledge the correction
    # naturally and move the flow forward, not re-ask for the same field or
    # restart the sequence from scratch.
    {
        "inputs": {
            "question": "Sorry, actually he just moved -- it's Tucson now, not Phoenix.",
            "history": [
                {"role": "user", "content": "Looking for a nursing home for my dad, he's had a couple of falls."},
                {"role": "assistant", "content": "I'm so sorry to hear that -- falls are one of the clearest signs families notice when more support is needed. Skilled nursing gives around-the-clock care. Would you like me to explore some options?"},
                {"role": "user", "content": "Yes please."},
                {"role": "assistant", "content": "To find the closest options for you -- what city or state are you in?"},
                {"role": "user", "content": "Phoenix, Arizona."},
            ],
        },
    },

    # Phase 4 -> Phase 5 transition: all core details already given. Next
    # reply should move into Phase 5 Step 1 (offer human support) + Step 2
    # (ask permission) -- must NOT ask for phone/email yet, since contact
    # permission hasn't been separately given.
    {
        "inputs": {
            "question": "That's about everything I can think of.",
            "history": [
                {"role": "user", "content": "My dad fell twice this week, I'm really worried."},
                {"role": "assistant", "content": "I'm so sorry -- that's stressful. Would you like me to explore some options near you?"},
                {"role": "user", "content": "Yes, please."},
                {"role": "assistant", "content": "To find the closest options for you -- what city or ZIP code are you in?"},
                {"role": "user", "content": "Tucson, AZ."},
                {"role": "assistant", "content": "Thank you. Roughly how old is your dad?"},
                {"role": "user", "content": "He's 82."},
                {"role": "assistant", "content": "Got it. Is he currently living alone, or with family nearby?"},
                {"role": "user", "content": "Alone, which is part of why I'm worried."},
                {"role": "assistant", "content": "That makes sense. Do you have a rough sense of the monthly budget you're working with? Many facilities accept Medicare or Medicaid, so there are often more options than expected."},
                {"role": "user", "content": "Maybe $3,500 a month."},
            ],
        },
    },

    # Phase 5 contact ask, right after the human-support permission was
    # given. Must ask for contact info NATURALLY (e.g. "what's the best
    # number or email"), never coldly ("Can I have your phone number?").
    {
        "inputs": {
            "question": "Sure, that would help.",
            "history": [
                {"role": "user", "content": "My dad fell twice this week, I'm really worried."},
                {"role": "assistant", "content": "I'm so sorry -- that's stressful. Would you like me to explore some options near you?"},
                {"role": "user", "content": "Yes, please."},
                {"role": "assistant", "content": "To find the closest options for you -- what city or ZIP code are you in?"},
                {"role": "user", "content": "Tucson, AZ."},
                {"role": "assistant", "content": "Thank you. I can have one of our care advisors walk you through some options in more detail and help you compare them side by side. Would you like that kind of personal support?"},
            ],
        },
    },

    # objection: "just looking" -- raised right after a permission ask.
    # Should use the objection-handling script (reassurance + asking if
    # it's for a parent/someone else), not push for details or contact info.
    {
        "inputs": {
            "question": "I'm just looking around, not ready for anything yet.",
            "history": [
                {"role": "user", "content": "I'm exploring options for my mother, she's getting more forgetful."},
                {"role": "assistant", "content": "I'm really sorry you're noticing that -- it can be heartbreaking to watch. Memory changes like these are often an early sign of cognitive decline, and Memory Care communities are built specifically for this, with structured routines and trained staff. Would you like me to find some options near you?"},
            ],
        },
    },

    # objection: "can't afford it" -- should reassure the service is free
    # and mention Medicaid/Medicare, not push forward or demand contact info.
    {
        "inputs": {
            "question": "I don't think we can afford any of this.",
            "history": [
                {"role": "user", "content": "My mother needs more help than I can give her at home."},
                {"role": "assistant", "content": "That sounds like a lot to carry. Assisted living communities are built for exactly this kind of support. Would you like me to explore some options?"},
            ],
        },
    },

    # objection: "need to think about it" -- mid detail-collection. Should
    # reassure (no rush, no commitment) rather than repeating the pending
    # question or pressuring the user to continue.
    {
        "inputs": {
            "question": "Actually, I need to think about it before deciding anything.",
            "history": [
                {"role": "user", "content": "We're looking into hospice care for my father."},
                {"role": "assistant", "content": "I'm so sorry you're going through this. Hospice and Palliative Care communities focus on comfort and dignity at this stage. Would you like me to find some options near you?"},
                {"role": "user", "content": "Yes."},
                {"role": "assistant", "content": "To find the closest options -- what city or state are you in?"},
            ],
        },
    },

    # objection: "we're managing at home" -- should validate their current
    # setup and softly offer to have options on hand, not pressure.
    {
        "inputs": {
            "question": "We're managing at home for now, just wanted to see what's out there.",
            "history": [
                {"role": "user", "content": "My mom's been living alone since my dad passed, I worry about her."},
                {"role": "assistant", "content": "I'm truly sorry for your loss. Loneliness at this stage has a bigger health impact than people realize. Would you like me to look into some options that could help?"},
            ],
        },
    },
]


# create the dataset
dataset_name = "Infomary Agent: Lead Generation Behavior 1.0"


async def generate_dataset():
    client = get_langsmith_client()
    if not client.has_dataset(dataset_name=dataset_name):
        dataset = client.create_dataset(dataset_name=dataset_name)
        client.create_examples(
            dataset_id=dataset.id,
            examples=examples
    )
    print(f"Successfully generated dataset:{dataset_name}")



# import asyncio
# asyncio.run(generate_dataset())
