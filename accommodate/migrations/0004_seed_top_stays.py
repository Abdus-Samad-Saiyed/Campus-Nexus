import uuid
from django.db import migrations

SEEDED_STAYS = [
    # Bengaluru
    {
        "name": "Zolo Stays - HSR Sector 2",
        "formatted_address": "14th Main Rd, Sector 2, HSR Layout, Bengaluru, Karnataka 560102",
        "city": "Bengaluru",
        "location_within_city": "HSR Layout Sector 2",
        "latitude": 12.911600,
        "longitude": 77.638900,
        "google_place_id": "ChIJbU123_HSR_ZOLO_01",
        "maps_url": "https://maps.google.com/?q=Zolo+Stays+HSR+Layout+Bengaluru",
        "usage_count": 14,
    },
    {
        "name": "Stanza Living - Manyata (Nagavara)",
        "formatted_address": "Near Manyata Embassy Business Park, Outer Ring Rd, Nagavara, Bengaluru, Karnataka 560045",
        "city": "Bengaluru",
        "location_within_city": "Nagavara, Near Manyata Tech Park",
        "latitude": 13.047800,
        "longitude": 77.621400,
        "google_place_id": "ChIJbU123_MANYATA_STANZA_02",
        "maps_url": "https://maps.google.com/?q=Stanza+Living+Manyata+Nagavara+Bengaluru",
        "usage_count": 11,
    },
    {
        "name": "Boston Living - Bellandur",
        "formatted_address": "Near RMZ Ecospace, Outer Ring Rd, Bellandur, Bengaluru, Karnataka 560103",
        "city": "Bengaluru",
        "location_within_city": "Bellandur / EcoSpace",
        "latitude": 12.926000,
        "longitude": 77.676200,
        "google_place_id": "ChIJbU123_BELLANDUR_BOSTON_03",
        "maps_url": "https://maps.google.com/?q=Boston+Living+Bellandur+Bengaluru",
        "usage_count": 9,
    },
    {
        "name": "Colive Primus - Electronic City",
        "formatted_address": "Hosur Rd, Phase 1, Electronic City, Bengaluru, Karnataka 560100",
        "city": "Bengaluru",
        "location_within_city": "Electronic City Phase 1",
        "latitude": 12.845200,
        "longitude": 77.660200,
        "google_place_id": "ChIJbU123_ECITY_COLIVE_04",
        "maps_url": "https://maps.google.com/?q=Colive+Primus+Electronic+City+Bengaluru",
        "usage_count": 7,
    },
    {
        "name": "Zolo Stays - Koramangala 4th Block",
        "formatted_address": "80 Feet Rd, 4th Block, Koramangala, Bengaluru, Karnataka 560034",
        "city": "Bengaluru",
        "location_within_city": "Koramangala 4th Block",
        "latitude": 12.934400,
        "longitude": 77.628800,
        "google_place_id": "ChIJbU123_KORAMANGALA_ZOLO_05",
        "maps_url": "https://maps.google.com/?q=Zolo+Stays+Koramangala+Bengaluru",
        "usage_count": 6,
    },
    # Hyderabad
    {
        "name": "Boston Living - Gachibowli",
        "formatted_address": "Financial District, Nanakramguda, Gachibowli, Hyderabad, Telangana 500032",
        "city": "Hyderabad",
        "location_within_city": "Financial District / Gachibowli",
        "latitude": 17.414000,
        "longitude": 78.348900,
        "google_place_id": "ChIJbU123_HYD_GACHIBOWLI_06",
        "maps_url": "https://maps.google.com/?q=Boston+Living+Financial+District+Hyderabad",
        "usage_count": 10,
    },
    {
        "name": "Stanza Living - Hitec City (Madhapur)",
        "formatted_address": "Near Mindspace IT Park, Madhapur, Hitec City, Hyderabad, Telangana 500081",
        "city": "Hyderabad",
        "location_within_city": "Madhapur / Hitec City",
        "latitude": 17.443500,
        "longitude": 78.377200,
        "google_place_id": "ChIJbU123_HYD_MADHAPUR_07",
        "maps_url": "https://maps.google.com/?q=Stanza+Living+Madhapur+Hyderabad",
        "usage_count": 8,
    },
    {
        "name": "Isthara PG - Kondapur",
        "formatted_address": "Near Botanical Garden Rd, Kondapur, Hyderabad, Telangana 500084",
        "city": "Hyderabad",
        "location_within_city": "Kondapur",
        "latitude": 17.460000,
        "longitude": 78.361000,
        "google_place_id": "ChIJbU123_HYD_KONDAPUR_08",
        "maps_url": "https://maps.google.com/?q=Isthara+PG+Kondapur+Hyderabad",
        "usage_count": 5,
    },
    # Pune
    {
        "name": "Stanza Living - Hinjawadi Phase 1",
        "formatted_address": "Rajiv Gandhi Infotech Park, Phase 1, Hinjawadi, Pune, Maharashtra 411057",
        "city": "Pune",
        "location_within_city": "Hinjawadi Phase 1",
        "latitude": 18.591300,
        "longitude": 73.738900,
        "google_place_id": "ChIJbU123_PUNE_HINJAWADI_09",
        "maps_url": "https://maps.google.com/?q=Stanza+Living+Hinjawadi+Pune",
        "usage_count": 9,
    },
    {
        "name": "Zolo Stays - Viman Nagar",
        "formatted_address": "Near Symbiosis International University, Viman Nagar, Pune, Maharashtra 411014",
        "city": "Pune",
        "location_within_city": "Viman Nagar",
        "latitude": 18.567900,
        "longitude": 73.914300,
        "google_place_id": "ChIJbU123_PUNE_VIMANNAGAR_10",
        "maps_url": "https://maps.google.com/?q=Zolo+Stays+Viman+Nagar+Pune",
        "usage_count": 7,
    },
    {
        "name": "Ithaca PG - Kharadi",
        "formatted_address": "Near EON Free Zone, Kharadi, Pune, Maharashtra 411014",
        "city": "Pune",
        "location_within_city": "Kharadi (EON IT Park)",
        "latitude": 18.551400,
        "longitude": 73.952600,
        "google_place_id": "ChIJbU123_PUNE_KHARADI_11",
        "maps_url": "https://maps.google.com/?q=Ithaca+PG+Kharadi+Pune",
        "usage_count": 5,
    },
    # Delhi NCR
    {
        "name": "Stanza Living - Cyber City (Gurgaon)",
        "formatted_address": "DLF Phase 2, Near Cyber Hub, Gurugram, Haryana 122002",
        "city": "Delhi NCR",
        "location_within_city": "Cyber City (Gurgaon)",
        "latitude": 28.490000,
        "longitude": 77.090000,
        "google_place_id": "ChIJbU123_GURGAON_CYBER_12",
        "maps_url": "https://maps.google.com/?q=Stanza+Living+Cyber+City+Gurgaon",
        "usage_count": 8,
    },
    {
        "name": "CoHo Flatshare - Sector 62 (Noida)",
        "formatted_address": "Block B, Industrial Area, Sector 62, Noida, Uttar Pradesh 201309",
        "city": "Delhi NCR",
        "location_within_city": "Sector 62 (Noida)",
        "latitude": 28.628000,
        "longitude": 77.364900,
        "google_place_id": "ChIJbU123_NOIDA_SEC62_13",
        "maps_url": "https://maps.google.com/?q=CoHo+Sector+62+Noida",
        "usage_count": 6,
    },
    # Srinagar
    {
        "name": "Transit Lodging - TRC Rajbagh",
        "formatted_address": "Near Tourist Reception Centre, Maulana Azad Rd, Rajbagh, Srinagar, Jammu and Kashmir 190001",
        "city": "Srinagar",
        "location_within_city": "Rajbagh / TRC",
        "latitude": 34.072600,
        "longitude": 74.819800,
        "google_place_id": "ChIJbU123_SRINAGAR_TRC_14",
        "maps_url": "https://maps.google.com/?q=Tourist+Reception+Centre+Srinagar",
        "usage_count": 4,
    },
]


def seed_top_stays(apps, schema_editor):
    AccommodationStayLocation = apps.get_model('accommodate', 'AccommodationStayLocation')
    for stay_data in SEEDED_STAYS:
        AccommodationStayLocation.objects.update_or_create(
            google_place_id=stay_data["google_place_id"],
            defaults={
                "name": stay_data["name"],
                "formatted_address": stay_data["formatted_address"],
                "city": stay_data["city"],
                "city_normalized": stay_data["city"].strip().lower(),
                "location_within_city": stay_data["location_within_city"],
                "location_within_city_normalized": stay_data["location_within_city"].strip().lower(),
                "latitude": stay_data["latitude"],
                "longitude": stay_data["longitude"],
                "maps_url": stay_data["maps_url"],
                "is_seeded": True,
                "usage_count": stay_data["usage_count"],
            }
        )


def remove_top_stays(apps, schema_editor):
    AccommodationStayLocation = apps.get_model('accommodate', 'AccommodationStayLocation')
    google_place_ids = [s["google_place_id"] for s in SEEDED_STAYS]
    AccommodationStayLocation.objects.filter(google_place_id__in=google_place_ids, is_seeded=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accommodate', '0003_accommodationgroup_accom_grp_city_trgm_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_top_stays, reverse_code=remove_top_stays),
    ]
