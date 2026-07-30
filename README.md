## Inspiration
We saw how small business owners suffer due to poor management of stocks, sales, and outstanding balances, so we made **EXPENSA**. It is a simple, intuitive software designed to allow owners to manage their businesses properly.

## What it does
* EXPENSA streamlines business management through data tracking and automated analysis *  Tracks all available inventory and stock levels automatically.
*  Manages customer accounts receivable to monitor who still owes money.
*  Provides AI-driven insights highlighting low stock and item reorder needs.
*  Delivers a daily AI brief about business health and daily activity.
*  Boosts overall sales by offering tailored business strategy recommendations

## How we built it
**Backend**: Built with Python to handle business logic and database interactions.
**Frontend**: Crafted using clean HTML to provide a functional interface.
**Database**: Powered by Vercel SQL Database (Neon) for live production data.
**Deployment**: Hosted online using the Vercel cloud deployment platform.
**AI Integration**: Leveraged Claude to power the analytical insights and briefs.
## Challenges we ran into
*  We originally built the entire application using an _SQLite database_. However, we discovered during production that SQLite databases are read-only when deployed on Vercel . *  We had to completely rewrite our database logic to migrate to a live cloud database that was Vercel SQL Database (Neon) .
*  Designing an interface with pure frontend language HTML was also difficult task, but we managed to design it .
## Accomplishments that we're proud of
*   Thought about a real-world idea that directly impacts and helps local business owners.
*   Successfully  updated our database architecture , due to issues with vercel and SQLite database
*   Built a functional, multi-layered application that connects AI to retail data.
*   Was able to complete our project on time even we started late
*   Added a good opening page even frontend was not our cup of tea
## What we learned
*   Building production-ready applications which can work with Vercel deployment.
*   Using cloud-hosted PostgreSQL databases (Neon) into a Python backend.
*   Learning about frontend 
*  Managing dynamic application states without  using local storage files.
## What's next for EXPENSA (An app to manage business better)
**UI  improvement** To improve the Ui and make it more appealing.
**Adding new features** to add more features which may help owners.
**Consumers** : Find clients for the app.
**Mobile Deployment** : Launch the web app for mobile.
**AI MODELS** :Add more ai features and different AI models.
