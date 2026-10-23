"""Interface text in English and Marathi.

English is written directly in the templates and in app.py, wrapped as _('...') or tr("...").
MR maps each English text to Marathi; anything missing is shown in English. Text with
{placeholders} is filled in after translation, so a translation can move them around.
Data stored in English in the database (animal types, statuses, vaccines...) is translated
for display only. tests/test_i18n.py checks that every text has a Marathi entry.
Text people type themselves (animal names, notes, products...) is machine-translated instead:
see machine_translate below and data_text in app.py.
"""
import html
from concurrent.futures import ThreadPoolExecutor, wait

from deep_translator import MyMemoryTranslator

LANGS = {"en": "English", "mr": "मराठी"}

BRAND = "VrushaA2 Dairy Tech"
COMPANY = "VrushaA2 Dairy Tech Pvt. Ltd."
TAGLINE = "From sanskar to prosperity"

MR = {
    # ---------------------------------------------------------------- brand and website
    BRAND: "वृषाA2 डेरीटेक",
    COMPANY: "वृषाA2 डेरीटेक प्रा. लि.",
    TAGLINE: "संस्कारातून समृद्धीकडे",
    "Sangli": "सांगली",
    "Home": "मुख्यपृष्ठ",
    "Website": "वेबसाइट",
    "Our website": "आमची वेबसाइट",
    "Language": "भाषा",
    "About us": "आमच्याविषयी",
    "Products": "उत्पादने",
    "How to order": "ऑर्डर कशी करावी",
    "Contact": "संपर्क",
    "Pure A2 milk from desi cows": "देशी गायींचे शुद्ध A2 दूध",
    "VrushaA2 Dairy Tech Pvt. Ltd.: A2 milk, ghee and dairy products from desi cows, delivered to your door.":
        "वृषाA2 डेरीटेक प्रा. लि.: देशी गायींचे A2 दूध, तूप आणि दुग्धजन्य पदार्थ, थेट तुमच्या घरी.",
    "Pure A2 milk, ghee and dairy products from desi cows that we raise with care, the traditional way.":
        "आम्ही प्रेमाने आणि पारंपरिक पद्धतीने सांभाळलेल्या देशी गायींचे शुद्ध A2 दूध, तूप आणि दुग्धजन्य पदार्थ.",
    "Order fresh dairy": "ताजे दुग्धपदार्थ मागवा",
    "See our products": "आमची उत्पादने पाहा",
    "desi cattle in our care": "देशी गोवंश आमच्या देखभालीत",
    "dairy products": "दुग्धजन्य उत्पादने",
    "milk from indigenous breeds": "देशी जातींचे दूध",
    "pay on delivery": "घरपोच मिळाल्यावर पैसे",
    "Our values": "आमची मूल्ये",
    "VrushaA2 Dairy Tech brings the goodness of the Indian desi cow to your home. Our work rests on sanskar: "
    "caring for every animal, honest food and respect for tradition.":
        "वृषाA2 डेरीटेक भारतीय देशी गायीचे सत्त्व तुमच्या घरापर्यंत पोहोचवते. आमचे काम संस्कारांवर उभे आहे: "
        "प्रत्येक जनावराची काळजी, प्रामाणिक अन्न आणि परंपरेचा आदर.",
    "Desi cows": "देशी गायी",
    "Indigenous Indian breeds such as Gir and Khillar, known for their A2 milk.":
        "गीर, खिल्लार यांसारख्या भारतीय देशी जाती, ज्या A2 दुधासाठी ओळखल्या जातात.",
    "Natural feed and care": "नैसर्गिक चारा आणि निगा",
    "Green fodder, clean water and regular check-ups and vaccinations, recorded for every animal.":
        "हिरवा चारा, स्वच्छ पाणी आणि नियमित तपासणी व लसीकरण, प्रत्येक जनावराची नोंद ठेवून.",
    "Fresh and clean": "ताजे आणि स्वच्छ",
    "Milked every morning and evening, handled hygienically and delivered fresh.":
        "दररोज सकाळ-संध्याकाळ दोहन, स्वच्छतेने हाताळणी आणि ताजे वितरण.",
    "Rooted in tradition": "परंपरेशी नाळ",
    "We follow the Hindu calendar for gau seva and havan. From sanskar to prosperity.":
        "गोसेवा आणि हवनासाठी आम्ही हिंदू पंचांग पाळतो. संस्कारातून समृद्धीकडे.",
    "Our products": "आमची उत्पादने",
    "Order": "ऑर्डर",
    "Out of stock": "साठा संपला",
    "Our product list is coming soon.": "आमची उत्पादन यादी लवकरच येत आहे.",
    "Today's panchang": "आजचे पंचांग",
    "Calculated for sunrise at {place}. Customers can see the full Hindu calendar after logging in.":
        "{place} येथील सूर्योदयानुसार. ग्राहकांना लॉग इन केल्यावर संपूर्ण हिंदू पंचांग पाहता येते.",
    "Create an account": "खाते तयार करा",
    "Sign up with your name, phone and delivery address.": "तुमचे नाव, फोन आणि पत्ता देऊन नोंदणी करा.",
    "Choose products": "उत्पादने निवडा",
    "Add milk, ghee, curd and more to your cart.": "दूध, तूप, दही आणि इतर पदार्थ कार्टमध्ये टाका.",
    "Pay on delivery": "घरपोच मिळाल्यावर पैसे द्या",
    "We confirm your order and deliver it to your door.": "आम्ही तुमची ऑर्डर निश्चित करून घरपोच देतो.",
    "Go to the shop": "दुकानात चला",
    "Staff and customer login": "कर्मचारी व ग्राहक लॉग इन",

    # ---------------------------------------------------------------- menu, login, account
    "Dashboard": "डॅशबोर्ड",
    "Cattle": "जनावरे",
    "Records": "नोंदी",
    "Health": "आरोग्य",
    "Vaccinations": "लसीकरण",
    "Feeding": "चारा",
    "Breeding": "प्रजनन",
    "Milk production": "दूध उत्पादन",
    "Shop": "दुकान",
    "Storefront": "ग्राहक दुकान",
    "Products and stock": "उत्पादने आणि साठा",
    "Customer orders": "ग्राहकांच्या ऑर्डर",
    "Tasks": "कामे",
    "due today or overdue": "आज देय किंवा थकीत",
    "Calendar": "पंचांग",
    "Users": "वापरकर्ते",
    "My orders": "माझ्या ऑर्डर",
    "Log in": "लॉग इन",
    "Log out": "बाहेर पडा",
    "Username": "वापरकर्ता नाव",
    "Password": "पासवर्ड",
    "New customer?": "नवीन ग्राहक?",
    "to order dairy products.": "आणि दुग्धजन्य पदार्थ मागवा.",
    "Back to the website": "वेबसाइटवर परत",
    "Create account": "खाते तयार करा",
    "Create a customer account": "ग्राहक खाते तयार करा",
    "Full name": "पूर्ण नाव",
    "Phone": "फोन",
    "Email": "ईमेल",
    "Address": "पत्ता",
    "Delivery address": "पोहोचवण्याचा पत्ता",
    "Back to log in": "लॉग इनकडे परत",
    "My profile": "माझे प्रोफाइल",
    "Role": "भूमिका",
    "Admin": "प्रशासक",
    "Staff": "कर्मचारी",
    "Customer": "ग्राहक",
    "Change password": "पासवर्ड बदला",
    "Current password": "सध्याचा पासवर्ड",
    "New password": "नवीन पासवर्ड",
    "Error": "त्रुटी",
    "Go home": "मुख्यपृष्ठावर जा",

    # ---------------------------------------------------------------- common words
    "All": "सर्व",
    "Add": "जोडा",
    "Edit": "संपादन",
    "Delete": "हटवा",
    "Remove": "काढा",
    "Save": "जतन करा",
    "Cancel": "रद्द करा",
    "Print": "छापा",
    "Filter": "फिल्टर",
    "From": "पासून",
    "To": "पर्यंत",
    "Date": "दिनांक",
    "Today": "आज",
    "Notes": "टीप",
    "Name": "नाव",
    "Type": "प्रकार",
    "Status": "स्थिती",
    "Yes": "होय",
    "Other": "इतर",
    "Overdue": "थकीत",
    "Done": "पूर्ण",
    "Open": "बाकी",
    "Pending": "प्रलंबित",
    "Confirmed": "निश्चित",
    "Active": "सक्रिय",
    "Disabled": "बंद",
    "Hidden": "लपवलेले",
    "{y} y {m} m": "{y} वर्षे {m} महिने",
    "{m} m": "{m} महिने",

    # ---------------------------------------------------------------- dashboard
    "Farm dashboard": "गोठा डॅशबोर्ड",
    "Tasks today": "आजची कामे",
    "{n} overdue": "{n} थकीत",
    "all tasks": "सर्व कामे",
    "Active cattle": "सक्रिय जनावरे",
    "{n} female": "{n} मादी",
    "Milk today": "आजचे दूध",
    "{n} L this month": "या महिन्यात {n} लि.",
    "Pregnant": "गाभण",
    "confirmed, not yet calved": "निश्चित, अजून वेत नाही",
    "Vaccines due": "देय लसी",
    "overdue or in next 14 days": "थकीत किंवा पुढील 14 दिवसांत",
    "Pending orders": "प्रलंबित ऑर्डर",
    "review": "पाहा",
    "Shop sales": "दुकान विक्री",
    "this month": "या महिन्यात",
    "Tasks this week": "या आठवड्यातील कामे",
    "Task": "काम",
    "Nothing scheduled for the next 7 days.": "पुढील 7 दिवसांत काहीही नियोजित नाही.",
    "Milk collected, last 14 days (litres)": "गेल्या 14 दिवसांचे दूध संकलन (लिटर)",
    "Daily milk in litres for the last 14 days": "गेल्या 14 दिवसांचे रोजचे दूध, लिटरमध्ये",
    "Highest day: {n} L.": "सर्वाधिक दिवस: {n} लि.",
    "Milk records": "दूध नोंदी",
    "Vaccinations due": "देय लसीकरण",
    "Nothing due in the next 14 days.": "पुढील 14 दिवसांत काहीही देय नाही.",
    "Upcoming calvings": "येणारी वेते",
    "No confirmed pregnancies.": "निश्चित गाभण नोंदी नाहीत.",
    "Recent health events": "अलीकडील आरोग्य नोंदी",
    "No health records yet.": "अजून आरोग्य नोंदी नाहीत.",

    # ---------------------------------------------------------------- tasks
    "Due": "देय दिनांक",
    "Animals": "जनावरे",
    "Assigned to": "जबाबदार",
    "Whole herd": "संपूर्ण कळप",
    "whole herd": "संपूर्ण कळप",
    "Opens the record form, filled in; saving it completes the task":
        "भरलेला नोंद फॉर्म उघडतो; जतन केल्यावर काम पूर्ण होते",
    "Delete this task?": "हे काम हटवायचे?",
    "Delete task": "काम हटवा",
    "New task": "नवीन काम",
    "Edit task": "काम संपादित करा",
    "Everyone's tasks": "सर्वांची कामे",
    "Assigned to me": "माझी कामे",
    "{n} open.": "{n} बाकी.",
    "For vaccination, health, feeding, breeding and milking tasks, Done → opens that record form already "
    "filled in; saving it completes the task, and a repeating task schedules its next date.":
        "लसीकरण, आरोग्य, चारा, प्रजनन आणि दोहनाच्या कामांसाठी «पूर्ण →» तो नोंद फॉर्म आधीच भरून उघडतो; "
        "तो जतन केल्यावर काम पूर्ण होते आणि पुन्हा होणाऱ्या कामाची पुढची तारीख ठरते.",
    "No open tasks. Schedule one with + New task or from a day on the calendar.":
        "बाकी कामे नाहीत. «+ नवीन काम» ने किंवा पंचांगातील दिवसावरून काम ठरवा.",
    "Recently done": "अलीकडे पूर्ण",
    "What": "काय",
    "Item": "वस्तू",
    "(vaccine, feed, treatment...)": "(लस, चारा, उपचार...)",
    "Title": "शीर्षक",
    "(blank = made from what and item)": "(रिकामे = «काय» आणि «वस्तू» वरून)",
    "Repeat": "पुनरावृत्ती",
    "Nobody yet": "अजून कोणी नाही",
    "Whole herd (all active animals)": "संपूर्ण कळप (सर्व सक्रिय जनावरे)",
    "(hold Ctrl or ⌘ to pick several; none = not about particular animals)":
        "(अनेक निवडण्यासाठी Ctrl किंवा ⌘ दाबून ठेवा; काहीच नाही = विशिष्ट जनावरासाठी नाही)",
    "For a havan, check the calendar: 🔥 Agni on Prithvi (earth) is the day for havan. "
    "You will be warned if the chosen day is not.":
        "हवनासाठी पंचांग पाहा: 🔥 अग्नी पृथ्वीवर असलेला दिवस हवनाचा. "
        "निवडलेला दिवस तसा नसल्यास तुम्हाला सूचना मिळेल.",
    "Save task": "काम जतन करा",
    "Please choose a date for the task.": "कृपया कामासाठी दिनांक निवडा.",
    "Task saved: {title}, {date}.": "काम जतन: {title}, {date}.",
    "Task done: {title}.": "काम पूर्ण: {title}.",
    "Next one scheduled for {date}.": "पुढचे काम {date} रोजी ठरवले.",
    "{date}: {note}.": "{date}: {note}.",
    "Next day with Agni on earth: {date}.": "अग्नी पृथ्वीवर असलेला पुढचा दिवस: {date}.",
    "Task deleted.": "काम हटवले.",
    "Task: {title}": "काम: {title}",
    # kinds and repeats (TASK_KINDS, REPEATS in app.py)
    "Vaccination": "लसीकरण",
    "Health / deworming": "आरोग्य / जंतनाशक",
    "Breeding / AI": "प्रजनन / कृत्रिम रेतन",
    "Milking": "दोहन",
    "Havan / puja": "हवन / पूजा",
    "Does not repeat": "एकदाच",
    "Every day": "दररोज",
    "Every week": "दर आठवड्याला",
    "Every month": "दर महिन्याला",
    "Every year": "दरवर्षी",

    # ---------------------------------------------------------------- calendar
    "Hindu calendar": "हिंदू पंचांग",
    "Shaka": "शके",
    "Vikram Samvat": "विक्रम संवत",
    "27 Nakshatras": "27 नक्षत्रे",
    "Adhika": "अधिक",
    "amanta month (new moon to new moon)": "अमान्त महिना (अमावास्या ते अमावास्या)",
    "This is an extra (adhika) month: the sun does not change rashi during it.":
        "हा अधिक महिना आहे: या काळात सूर्य राशी बदलत नाही.",
    "Mon · Soma": "सोम",
    "Tue · Mangala": "मंगळ",
    "Wed · Budha": "बुध",
    "Thu · Guru": "गुरु",
    "Fri · Shukra": "शुक्र",
    "Sat · Shani": "शनि",
    "Sun · Ravi": "रवि",
    "Schedule a task on {day}": "{day} रोजी काम ठरवा",
    "ruled by {planet}": "स्वामी: {planet}",
    "Moon in {rashi}": "चंद्र {rashi} राशीत",
    "Agni": "अग्नी",
    "Agni Nakshatra": "अग्नी नक्षत्र",
    "Agni Nakshatra (Kathiri)": "अग्नी नक्षत्र (कत्तिरी)",
    "Sun from Bharani pada 3 to Rohini pada 1": "सूर्य भरणीच्या तिसऱ्या चरणापासून रोहिणीच्या पहिल्या चरणापर्यंत",
    "Sunrise": "सूर्योदय",
    "{n} farm events": "गोठ्यातील {n} नोंदी",
    "Tithi and nakshatra are shown as they stand at sunrise in {place}, calculated with the PyEphem astronomy "
    "library and the Lahiri ayanamsa; on days when a tithi or nakshatra changes near sunrise, check a local "
    "panchang for muhurta timing.":
        "तिथी आणि नक्षत्र {place} येथील सूर्योदयाच्या वेळी जसे असतात तसे दाखवले आहेत; ते PyEphem खगोलशास्त्र "
        "लायब्ररी आणि लाहिरी अयनांशाने काढले आहेत. ज्या दिवशी तिथी किंवा नक्षत्र सूर्योदयाजवळ बदलते, त्या "
        "दिवशी मुहूर्तासाठी स्थानिक पंचांग पाहा.",
    "Agni is the Agnivas used for havan: from the tithi and weekday, Agni is on earth (Prithvi, good for havan), "
    "in the sky (Akasha) or in the underworld (Patala), when havan is not advised.":
        "अग्नी म्हणजे हवनासाठी पाहिला जाणारा अग्निवास: तिथी आणि वारावरून अग्नी पृथ्वीवर (हवनासाठी शुभ), "
        "आकाशात किंवा पाताळात (हवन टाळावे) असतो.",
    "Days with an orange border fall in Agni Nakshatra (Kathiri), when the sun moves from Bharani pada 3 to "
    "Rohini pada 1, about 4 to 28 May.":
        "नारिंगी कडा असलेले दिवस अग्नी नक्षत्रात (कत्तिरी) येतात, जेव्हा सूर्य भरणीच्या तिसऱ्या चरणापासून "
        "रोहिणीच्या पहिल्या चरणापर्यंत जातो, साधारण 4 ते 28 मे.",
    "Red lines are farm events: vaccines due, expected calvings and scheduled tasks. Use + on a day to "
    "schedule a task.":
        "लाल ओळी गोठ्यातील नोंदी आहेत: देय लसी, अपेक्षित वेत आणि ठरवलेली कामे. काम ठरवण्यासाठी दिवसावरील "
        "+ वापरा.",
    "The year at a glance": "वर्ष एका नजरेत",
    "Month": "महिना",
    "Starts (Pratipada)": "सुरुवात (प्रतिपदा)",
    "Ends (Amavasya)": "शेवट (अमावास्या)",
    "Vaccine due: {tag} ({vaccine})": "लस देय: {tag} ({vaccine})",
    "Calving expected: {tag}": "अपेक्षित वेत: {tag}",
    "Nakshatras": "नक्षत्रे",
    "The 27 Nakshatras": "27 नक्षत्रे",
    "Today ({day}, at sunrise)": "आज ({day}, सूर्योदयाला)",
    "Nakshatra": "नक्षत्र",
    "nakshatra": "नक्षत्र",
    "pada": "चरण",
    "Span (sidereal)": "विस्तार (निरयन)",
    "Rashi": "राशी",
    "Ruling planet": "स्वामी ग्रह",
    "Deity": "देवता",
    "Symbol": "प्रतीक",
    "Each nakshatra spans 13°20′ of the zodiac and has four padas of 3°20′. Some nakshatras cross from one "
    "rashi into the next; the rashi shown is where it begins.":
        "प्रत्येक नक्षत्र राशिचक्राचा 13°20′ भाग व्यापते आणि त्याचे 3°20′ चे चार चरण असतात. काही नक्षत्रे "
        "एका राशीतून पुढच्या राशीत जातात; दाखवलेली राशी नक्षत्र जिथे सुरू होते ती आहे.",
    # paksha, tithi, weekday, rashi (panchang.py)
    "Shukla": "शुक्ल", "Krishna": "कृष्ण",
    "Pratipada": "प्रतिपदा", "Dwitiya": "द्वितीया", "Tritiya": "तृतीया", "Chaturthi": "चतुर्थी",
    "Panchami": "पंचमी", "Shashthi": "षष्ठी", "Saptami": "सप्तमी", "Ashtami": "अष्टमी", "Navami": "नवमी",
    "Dashami": "दशमी", "Ekadashi": "एकादशी", "Dwadashi": "द्वादशी", "Trayodashi": "त्रयोदशी",
    "Chaturdashi": "चतुर्दशी", "Purnima": "पौर्णिमा", "Amavasya": "अमावास्या",
    "Somavara": "सोमवार", "Mangalavara": "मंगळवार", "Budhavara": "बुधवार", "Guruvara": "गुरुवार",
    "Shukravara": "शुक्रवार", "Shanivara": "शनिवार", "Ravivara": "रविवार",
    "Mesha": "मेष", "Vrishabha": "वृषभ", "Mithuna": "मिथुन", "Karka": "कर्क", "Simha": "सिंह",
    "Kanya": "कन्या", "Tula": "तूळ", "Vrishchika": "वृश्चिक", "Dhanu": "धनु", "Makara": "मकर",
    "Kumbha": "कुंभ", "Meena": "मीन",
    # Agnivas notes (panchang.AGNIVAS)
    "Agni on earth: good for havan": "अग्नी पृथ्वीवर: हवनासाठी शुभ",
    "Agni in the sky: havan not advised (prana-nasha)": "अग्नी आकाशात: हवन टाळावे (प्राणनाश)",
    "Agni in the underworld: havan not advised (artha-nasha)": "अग्नी पाताळात: हवन टाळावे (अर्थनाश)",
    # nakshatra planets, deities and symbols (panchang.NAKSHATRAS)
    "Ketu": "केतू", "Venus": "शुक्र", "Sun": "सूर्य", "Moon": "चंद्र", "Mars": "मंगळ", "Rahu": "राहू",
    "Jupiter": "गुरू", "Saturn": "शनी", "Mercury": "बुध",
    "Ashwini Kumaras": "अश्विनीकुमार", "Yama": "यम", "Brahma (Prajapati)": "ब्रह्मा (प्रजापती)",
    "Soma": "सोम", "Rudra": "रुद्र", "Aditi": "अदिती", "Brihaspati": "बृहस्पती", "Nagas": "नाग",
    "Pitris": "पितर", "Bhaga": "भग", "Aryaman": "अर्यमा", "Savitr": "सविता", "Vishvakarma": "विश्वकर्मा",
    "Vayu": "वायू", "Indra and Agni": "इंद्र आणि अग्नी", "Mitra": "मित्र", "Indra": "इंद्र",
    "Nirriti": "निऋती", "Apas": "आप (जल)", "Vishvedevas": "विश्वेदेव", "Vishnu": "विष्णू",
    "Eight Vasus": "अष्टवसू", "Varuna": "वरुण", "Aja Ekapada": "अज एकपाद", "Ahirbudhnya": "अहिर्बुध्न्य",
    "Pushan": "पूषा",
    "Horse's head": "घोड्याचे मस्तक", "Yoni": "योनी", "Razor / flame": "वस्तरा / ज्योत", "Chariot": "रथ",
    "Deer's head": "हरणाचे मस्तक", "Teardrop": "अश्रुबिंदू", "Quiver of arrows": "भाता",
    "Cow's udder": "गाईची कास", "Coiled serpent": "वेटोळे घातलेला साप", "Royal throne": "राजसिंहासन",
    "Front legs of a bed": "पलंगाचे पुढचे पाय", "Back legs of a bed": "पलंगाचे मागचे पाय", "Hand": "हात",
    "Bright jewel": "तेजस्वी रत्न", "Young shoot": "कोवळा अंकुर", "Triumphal arch": "विजयतोरण",
    "Lotus": "कमळ", "Earring": "कुंडल", "Bunch of roots": "मुळांचा झुपका", "Winnowing fan": "सूप",
    "Elephant tusk": "हत्तीचा दात", "Ear": "कान", "Drum": "ढोल", "Empty circle": "रिकामे वर्तुळ",
    "Front of a funeral cot": "तिरडीचा पुढचा भाग", "Back of a funeral cot": "तिरडीचा मागचा भाग",
    "Fish": "मासा",

    # ---------------------------------------------------------------- cattle
    "Cattle register": "जनावरांची नोंदवही",
    "Add animal": "जनावर जोडा",
    "Search tag, name or breed": "टॅग, नाव किंवा जात शोधा",
    "{n} animals": "{n} जनावरे",
    "Tag": "टॅग",
    "Breed": "जात",
    "Sex": "लिंग",
    "Age": "वय",
    "Weight (kg)": "वजन (किलो)",
    "Remove {animal} and delete all its health, vaccination, feeding, breeding and milk records? "
    "This cannot be undone.":
        "{animal} काढायचे आणि त्याच्या सर्व आरोग्य, लसीकरण, चारा, प्रजनन व दूध नोंदी हटवायच्या? "
        "हे परत करता येणार नाही.",
    "No animals found.": "जनावरे सापडली नाहीत.",
    "Edit {tag}": "{tag} संपादित करा",
    "Tag number": "टॅग क्रमांक",
    "Date of birth": "जन्मतारीख",
    "Colour / markings": "रंग / खुणा",
    "Source": "स्रोत",
    "Purchase date": "खरेदी दिनांक",
    "Purchase price (₹)": "खरेदी किंमत (₹)",
    "Mother (dam)": "आई",
    "Unknown / not on farm": "माहीत नाही / गोठ्यात नाही",
    "Father (sire / semen)": "वडील (वळू / वीर्य)",
    "Photo": "फोटो",
    "Delete this animal and all its records?": "हे जनावर आणि त्याच्या सर्व नोंदी हटवायच्या?",
    "Photo of {tag}": "{tag} चा फोटो",
    "Born": "जन्म",
    "Colour": "रंग",
    "Weight": "वजन",
    "Mother": "आई",
    "Father": "वडील",
    "Calves": "वासरे",
    "Milk, 30 days": "दूध, 30 दिवस",
    "{litres} L over {days} days": "{days} दिवसांत {litres} लि.",
    "L/day": "लि./दिवस",
    "All tasks": "सर्व कामे",
    "No open tasks for this animal.": "या जनावरासाठी बाकी कामे नाहीत.",
    "No records yet.": "अजून नोंदी नाहीत.",
    "Tag number is required.": "टॅग क्रमांक आवश्यक आहे.",
    "Another animal already has that tag number.": "हा टॅग क्रमांक दुसऱ्या जनावराला आधीच दिला आहे.",
    "Animal saved.": "जनावर जतन केले.",
    "Animal and all its records deleted.": "जनावर आणि त्याच्या सर्व नोंदी हटवल्या.",
    "Photo must be a JPG, PNG, WEBP or GIF image.": "फोटो JPG, PNG, WEBP किंवा GIF प्रकारचा असावा.",
    # types, sexes, statuses, sources (app.py)
    "Cow": "गाय", "Buffalo": "म्हैस", "Bull": "वळू", "Ox": "बैल", "Calf": "वासरू", "Heifer": "कालवड",
    "Female": "मादी", "Male": "नर",
    "Sold": "विकले", "Dead": "मृत",
    "Born on farm": "गोठ्यात जन्म", "Purchased": "खरेदी", "Donated": "दान",
    # breeds (BREEDS)
    "Gir": "गीर", "Sahiwal": "साहिवाल", "Red Sindhi": "लाल सिंधी", "Tharparkar": "थारपारकर",
    "Khillar": "खिल्लार", "Deoni": "देवणी", "Kankrej": "कांकरेज", "Ongole": "ओंगोल", "Hallikar": "हल्लीकर",
    "HF Cross": "HF संकरित", "Jersey Cross": "जर्सी संकरित", "Murrah": "मुर्रा", "Mehsana": "मेहसाणा",
    "Jaffarabadi": "जाफराबादी", "Pandharpuri": "पंढरपुरी",

    # ---------------------------------------------------------------- records
    "Add {what}": "{what} जोडा",
    "Edit {what}": "{what} संपादित करा",
    "All animals": "सर्व जनावरे",
    "Animal": "जनावर",
    "Milk": "दूध",
    "Delete this record?": "ही नोंद हटवायची?",
    "No records.": "नोंदी नाहीत.",
    "Completing task {title} (due {date}).": "काम पूर्ण करत आहात: {title} (देय {date}).",
    "Check the details and save: the task is then marked done and the next one is scheduled.":
        "माहिती तपासा आणि जतन करा: त्यानंतर काम पूर्ण होईल आणि पुढचे काम ठरवले जाईल.",
    "Check the details and save: the task is then marked done.":
        "माहिती तपासा आणि जतन करा: त्यानंतर काम पूर्ण होईल.",
    "(hold Ctrl or ⌘ to pick several for a group entry)": "(गट नोंदीसाठी अनेक निवडायला Ctrl किंवा ⌘ दाबून ठेवा)",
    "Please choose an animal and fill the required fields.": "कृपया जनावर निवडा आणि आवश्यक माहिती भरा.",
    "Saved {n} {what}.": "{what} जतन: {n}.",
    "Record deleted.": "नोंद हटवली.",
    # RECORDS titles, record names (one and several), field labels and choices
    "Health records": "आरोग्य नोंदी",
    "Feeding records": "चारा नोंदी",
    "Breeding records": "प्रजनन नोंदी",
    "health record": "आरोग्य नोंद", "health records": "आरोग्य नोंदी",
    "vaccination": "लसीकरण", "vaccinations": "लसीकरणे",
    "feeding record": "चारा नोंद", "feeding records": "चारा नोंदी",
    "breeding record": "प्रजनन नोंद", "breeding records": "प्रजनन नोंदी",
    "milk record": "दूध नोंद", "milk records": "दूध नोंदी",
    "Diagnosis / symptoms": "निदान / लक्षणे",
    "Treatment / medicine": "उपचार / औषध",
    "Vet name": "पशुवैद्याचे नाव",
    "Cost (₹)": "खर्च (₹)",
    "Date given": "दिलेला दिनांक",
    "Vaccine": "लस",
    "Dose": "मात्रा",
    "Next due": "पुढील देय",
    "Given by": "कोणी दिली",
    "Feed": "चारा",
    "Quantity (kg)": "प्रमाण (किलो)",
    "Service date": "रेतन दिनांक",
    "Method": "पद्धत",
    "Bull / semen ID": "वळू / वीर्य क्रमांक",
    "Technician": "तंत्रज्ञ",
    "Pregnancy": "गर्भधारणा",
    "Expected calving (blank = auto)": "अपेक्षित वेत (रिकामे = आपोआप)",
    "Expected calving": "अपेक्षित वेत",
    "Actual calving": "प्रत्यक्ष वेत",
    "Calf tag no.": "वासराचा टॅग क्र.",
    "Session": "वेळ",
    "Litres": "लिटर",
    "Check-up": "तपासणी", "Illness": "आजार", "Treatment": "उपचार", "Deworming": "जंतनाशक",
    "Injury": "जखम", "Surgery": "शस्त्रक्रिया",
    "FMD (Foot and Mouth)": "FMD (लाळ्या खुरकुत)", "HS (Haemorrhagic Septicaemia)": "HS (घटसर्प)",
    "BQ (Black Quarter)": "BQ (फऱ्या)", "Brucellosis": "ब्रुसेलोसिस", "Theileriosis": "थायलेरियासिस",
    "Lumpy Skin Disease": "लम्पी त्वचा रोग", "Anthrax": "अँथ्रॅक्स (काळपुळी)", "Rabies": "रेबीज",
    "Green fodder": "हिरवा चारा", "Dry fodder": "कोरडा चारा", "Silage": "मुरघास", "Concentrate": "खुराक",
    "Cattle feed pellets": "पशुखाद्य गोळ्या", "Oil cake": "पेंड", "Mineral mixture": "खनिज मिश्रण",
    "Artificial insemination": "कृत्रिम रेतन", "Natural service": "नैसर्गिक रेतन",
    "Not pregnant": "गाभण नाही",
    "Morning": "सकाळ", "Evening": "संध्याकाळ",

    # ---------------------------------------------------------------- shop and orders
    "Farm-fresh dairy shop": "गोठ्यातील ताज्या दुग्धपदार्थांचे दुकान",
    "Cart": "कार्ट",
    "{n} in stock": "{n} साठ्यात",
    "Quantity": "प्रमाण",
    "Add to cart": "कार्टमध्ये टाका",
    "No products yet.": "अजून उत्पादने नाहीत.",
    "Your cart": "तुमची कार्ट",
    "Product": "उत्पादन",
    "Price": "किंमत",
    "Subtotal": "उप-एकूण",
    "Quantity of {product}": "{product} चे प्रमाण",
    "Remove {product} from cart": "{product} कार्टमधून काढा",
    "Total": "एकूण",
    "Update quantities": "प्रमाण अद्ययावत करा",
    "Set a quantity to 0 to remove it.": "काढण्यासाठी प्रमाण 0 करा.",
    "Delivery details": "पोहोचवण्याची माहिती",
    "Notes (delivery time, etc.)": "टीप (पोहोचवण्याची वेळ इ.)",
    "Place order": "ऑर्डर द्या",
    "Pay on delivery. Phone and address are required.": "घरपोच मिळाल्यावर पैसे द्या. फोन आणि पत्ता आवश्यक आहेत.",
    "Your cart is empty.": "तुमची कार्ट रिकामी आहे.",
    "Browse products": "उत्पादने पाहा",
    "Orders": "ऑर्डर",
    "Change status": "स्थिती बदला",
    "No orders yet.": "अजून ऑर्डर नाहीत.",
    "Placed": "दिली",
    "Qty": "प्रमाण",
    "Total (pay on delivery)": "एकूण (घरपोच मिळाल्यावर पैसे)",
    "Back to orders": "ऑर्डरकडे परत",
    "Delivered": "पोहोचवले",
    "Cancelled": "रद्द",
    "Category": "वर्ग",
    "Unit": "एकक",
    "Stock": "साठा",
    "Listed": "दुकानात",
    "Add product": "उत्पादन जोडा",
    "Edit {name}": "{name} संपादित करा",
    "Price (₹)": "किंमत (₹)",
    "Show in shop": "दुकानात दाखवा",
    "Description": "वर्णन",
    "Added {product} to your cart.": "{product} कार्टमध्ये टाकले.",
    "Removed {product} from your cart.": "{product} कार्टमधून काढले.",
    "the item": "ही वस्तू",
    "Please enter a phone number (at least 10 digits) and a delivery address.":
        "कृपया फोन नंबर (किमान 10 अंक) आणि पोहोचवण्याचा पत्ता भरा.",
    "Not enough stock for: {products}": "पुरेसा साठा नाही: {products}",
    "Order #{id} placed. We will confirm it soon.": "ऑर्डर #{id} दिली. आम्ही लवकरच ती निश्चित करू.",
    "Order #{id} marked {status}.": "ऑर्डर #{id}: {status}.",
    "Price and stock must be numbers.": "किंमत आणि साठा अंकात असावेत.",
    "Product saved.": "उत्पादन जतन केले.",
    # product categories (PRODUCT_CATEGORIES)
    "Ghee": "तूप", "Curd & Buttermilk": "दही व ताक", "Paneer": "पनीर", "Butter": "लोणी", "Sweets": "मिठाई",
    "Gomutra & Ayurvedic": "गोमूत्र व आयुर्वेदिक", "Organic manure": "सेंद्रिय खत",

    # ---------------------------------------------------------------- users
    "Add user": "वापरकर्ता जोडा",
    "Edit user": "वापरकर्ता संपादित करा",
    "Admins": "प्रशासक",
    "Farm staff": "गोठा कर्मचारी",
    "Customers": "ग्राहक",
    "Joined": "सामील",
    "Admins manage everything including users. Farm staff manage cattle, records, products and orders. "
    "Customers can only shop and see the calendar.":
        "प्रशासक वापरकर्त्यांसह सर्व काही सांभाळतात. गोठा कर्मचारी जनावरे, नोंदी, उत्पादने आणि ऑर्डर "
        "सांभाळतात. ग्राहक फक्त खरेदी करू शकतात आणि पंचांग पाहू शकतात.",
    "Add staff member": "कर्मचारी जोडा",
    "Passwords are set when a staff account is added and changed by their owner on My profile. "
    "An admin can reset only a staff member's password. Nobody can see a password.":
        "कर्मचाऱ्याचे खाते जोडताना पासवर्ड ठरवला जातो आणि तो त्याचा मालक «माझे प्रोफाइल» वर बदलतो. "
        "प्रशासक फक्त कर्मचाऱ्याचा पासवर्ड नव्याने ठरवू शकतो. कोणालाही पासवर्ड दिसत नाही.",
    "Reset password": "पासवर्ड नव्याने ठरवा",
    "For a staff member who has forgotten their password. Give them the new one; they can change it on My profile.":
        "पासवर्ड विसरलेल्या कर्मचाऱ्यासाठी. त्यांना नवीन पासवर्ड सांगा; ते तो «माझे प्रोफाइल» वर बदलू शकतात.",
    "Confirm new password": "नवीन पासवर्ड पुन्हा लिहा",
    "The two passwords do not match.": "दोन्ही पासवर्ड जुळत नाहीत.",
    "Password reset for {name}. Give them the new password; they can change it on My profile.":
        "{name} यांचा पासवर्ड नव्याने ठरवला. त्यांना नवीन पासवर्ड सांगा; ते तो «माझे प्रोफाइल» वर बदलू शकतात.",
    "Account active (can log in)": "खाते सक्रिय (लॉग इन करू शकतो)",
    "Wrong username or password, or the account is disabled.":
        "वापरकर्ता नाव किंवा पासवर्ड चुकीचा आहे, किंवा खाते बंद आहे.",
    "Password must be at least 6 characters.": "पासवर्ड किमान 6 अक्षरांचा असावा.",
    "That username is taken.": "हे वापरकर्ता नाव आधीच घेतले आहे.",
    "Welcome! Your account is ready.": "स्वागत आहे! तुमचे खाते तयार आहे.",
    "Current password is wrong; password not changed.": "सध्याचा पासवर्ड चुकीचा आहे; पासवर्ड बदलला नाही.",
    "New password must be at least 6 characters.": "नवीन पासवर्ड किमान 6 अक्षरांचा असावा.",
    "Profile saved.": "प्रोफाइल जतन केले.",
    "You cannot remove your own admin access.": "तुम्ही स्वतःचा प्रशासक प्रवेश काढू शकत नाही.",
    "User saved.": "वापरकर्ता जतन केला.",
    "Remove user": "वापरकर्ता काढा",
    "Remove the account of {name}? This cannot be undone.": "{name} यांचे खाते काढायचे? हे परत करता येणार नाही.",
    "{name} removed.": "{name} यांचे खाते काढले.",
    "You cannot remove your own account.": "तुम्ही स्वतःचे खाते काढू शकत नाही.",
    "{name} has {n} orders, so the account is kept for the order history. "
    "Untick 'Account active' to stop them logging in.":
        "{name} यांच्या {n} ऑर्डर आहेत, म्हणून ऑर्डरच्या इतिहासासाठी खाते ठेवले आहे. "
        "लॉग इन थांबवण्यासाठी «खाते सक्रिय» मधील खूण काढा.",
    "A customer with orders cannot be removed, so the order history stays complete; untick Account active instead.":
        "ऑर्डर असलेल्या ग्राहकाचे खाते काढता येत नाही, जेणेकरून ऑर्डरचा इतिहास पूर्ण राहील; त्याऐवजी "
        "«खाते सक्रिय» मधील खूण काढा.",

    # ---------------------------------------------------------------- errors
    "Form expired, please go back and try again.": "फॉर्मची वेळ संपली, कृपया मागे जाऊन पुन्हा प्रयत्न करा.",
    "You don't have access to this page.": "हे पान पाहण्याची तुम्हाला परवानगी नाही.",
    "Page not found.": "पान सापडले नाही.",
    "Some of the values entered are not valid. Please go back and check dates and numbers.":
        "भरलेली काही माहिती योग्य नाही. कृपया मागे जाऊन दिनांक आणि अंक तपासा.",
}

MONTHS_MR = ["जाने", "फेब्रु", "मार्च", "एप्रि", "मे", "जून", "जुलै", "ऑग", "सप्टें", "ऑक्टो", "नोव्हें", "डिसें"]
WEEKDAYS_MR = ["सोम", "मंगळ", "बुध", "गुरु", "शुक्र", "शनि", "रवि"]  # Monday first, like date.weekday()


MYMEMORY_LANGS = {"mr": "mr-IN"}
MAX_SOURCE = 500  # MyMemory takes up to 500 characters per text; longer notes stay in English


def machine_translate(texts, lang="mr", email=None, timeout=12, workers=6):
    """Translate English texts typed by people (names, notes, products) with the free MyMemory service.

    Uses the deep-translator library, several texts at a time. Returns {text: translation} for the
    texts that came back within `timeout` seconds; the rest are left out and can be tried again later.
    `email` raises MyMemory's free limit from 5,000 to 50,000 characters a day.
    """
    def one(text):
        # a translator per text: deep-translator keeps the request parameters on the object
        out = MyMemoryTranslator(source="en-GB", target=MYMEMORY_LANGS[lang], email=email).translate(text)
        if not out or "MYMEMORY WARNING" in out.upper():  # the daily limit notice comes back as the "translation"
            raise RuntimeError("MyMemory gave no translation")
        return html.unescape(out).strip()

    pool = ThreadPoolExecutor(max_workers=workers)
    jobs = {pool.submit(one, t): t for t in texts if t.strip() and len(t) <= MAX_SOURCE}
    done, _ = wait(jobs, timeout=timeout)
    pool.shutdown(wait=False, cancel_futures=True)  # requests have no timeout of their own; don't wait for them
    return {jobs[f]: f.result() for f in done if f.exception() is None}


def translate(text, lang, **kw):
    if lang == "mr":
        text = MR.get(text, text)
    return text.format(**kw) if kw else text


def date_text(d, fmt, lang):
    """strftime, with Marathi month (%b) and weekday (%a) names when lang is mr."""
    if lang != "mr":
        return d.strftime(fmt)
    return (d.strftime(fmt.replace("%b", "{b}").replace("%a", "{a}"))
            .replace("{b}", MONTHS_MR[d.month - 1]).replace("{a}", WEEKDAYS_MR[d.weekday()]))
