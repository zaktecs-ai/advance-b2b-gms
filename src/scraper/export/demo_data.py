"""In-repo demo data provider.

These ARE NOT scraped-from-Google records; they are representative fixtures so the
review-analysis add-on can be run and tested end-to-end without a live session.
In live operation the Playwright collector replaces this provider.
"""
from __future__ import annotations

from ..models import Business


def demo_businesses() -> list[Business]:
    return [
        _biz(
            "Brightline Pool & Spa", "Pool & Spa Service", "pool-place-001",
            rating=4.9, review_count=212,
            reviews=[
                "Excellent work, very professional and responsive team. The pool looks brand new.",
                "Fast, clean, and affordable. Highly recommend their service.",
                "Great communication and quality work. They kept us informed throughout.",
            ],
        ),
        _biz(
            "Summit Landscaping Co", "Landscaping", "land-place-002",
            rating=3.1, review_count=34,
            reviews=[
                "Slow service, waited weeks for a simple quote.",
                "Pricing was too high and communication was poor.",
                "Average work, nothing special.",
            ],
        ),
        _biz(
            "Metro Dental Studio", "Dentist", "dent-place-003",
            rating=4.7, review_count=568,
            reviews=[
                "The best dentist I've been to. Kind, friendly staff and spotless office.",
                "Financing options available and worth every penny.",
                "Very professional and knowledgeable. Painless experience.",
            ],
        ),
    ]


def _biz(name, category, pid, rating, review_count, reviews, **kw):
    b = Business(
        business_name=name,
        category=category,
        place_id=pid,
        kgmid="kg/" + pid,
        google_maps_url=f"https://www.google.com/maps?q={name.replace(' ', '+')}",
        phone="+1-555-0101",
        website=f"https://www.{name.lower().replace(' ', '')}.com",
        address="1 Main St",
        city="Dallas",
        state="TX",
        country="US",
        latitude=32.7767,
        longitude=-96.7970,
        plus_code="8666QV7V+9V",
        rating=rating,
        review_count=review_count,
        business_status="Open",
        website_status="LIVE",
        tech_stack=["WordPress", "GA4"],
        **kw,
    )
    b.reviews = reviews
    return b
