from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import httpx
import asyncio
import re
from .azure_clients import AzureClients


class DiaryPipeline:
    
    def __init__(self, azure_clients: AzureClients):
        self.azure_clients = azure_clients
    
    def analyze_sentiment(self, text: str) -> str:
        if not self.azure_clients.openai_client:
            return "neutral"
        
        try:
            response = self.azure_clients.openai_client.chat.completions.create(
                model=self.azure_clients.openai_deployment,
                messages=[
                    {"role": "system", "content": "You are a sentiment analyzer. Respond with only one word: 'positive', 'negative', or 'neutral'."},
                    {"role": "user", "content": f"Analyze the sentiment of this health diary entry: {text}"}
                ],
                temperature=0.3,
                max_tokens=10
            )
            sentiment = response.choices[0].message.content.strip().lower()
            if sentiment not in ["positive", "negative", "neutral"]:
                return "neutral"
            return sentiment
        except:
            return "neutral"
    
    def generate_summary(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not entries:
            return {
                "total_entries": 0,
                "date_range": {},
                "sentiment_trend": [],
                "common_symptoms": [],
                "mood_patterns": [],
                "suggestions": [],
                "visualization_data": {}
            }
        
        dates = [entry.get("timestamp", datetime.now()) for entry in entries]
        sentiments = [entry.get("sentiment", "neutral") for entry in entries]
        
        diseases = {}
        moods = {}
        for entry in entries:
            if entry.get("entry_type") in ["chronic_condition", "genetic_condition", "past_illness"]:
                text = entry.get("text", "").lower()
                common_diseases = ["diabetes", "hypertension", "asthma", "arthritis", "heart disease", "cancer", "thyroid", "copd", "depression", "anxiety"]
                for disease in common_diseases:
                    if disease in text:
                        diseases[disease] = diseases.get(disease, 0) + 1
            
            if entry.get("entry_type") == "mood":
                mood_text = entry.get("text", "").lower()
                if "happy" in mood_text or "good" in mood_text:
                    moods["positive"] = moods.get("positive", 0) + 1
                elif "sad" in mood_text or "bad" in mood_text:
                    moods["negative"] = moods.get("negative", 0) + 1
                else:
                    moods["neutral"] = moods.get("neutral", 0) + 1
        
        # Generate suggestions (will try async NCBI version, fallback to simple)
        try:
            suggestions = self._generate_suggestions(entries)
        except:
            suggestions = ["Consider maintaining regular sleep patterns", "Stay hydrated throughout the day"]
        
        sentiment_counts = {"positive": 0, "negative": 0, "neutral": 0}
        for sentiment in sentiments:
            sentiment_counts[sentiment] = sentiment_counts.get(sentiment, 0) + 1
        
        time_series = []
        for i, entry in enumerate(entries):
            time_series.append({
                "date": entry.get("timestamp", datetime.now()).isoformat(),
                "sentiment": entry.get("sentiment", "neutral"),
                "type": entry.get("entry_type", "food")
            })
        
        return {
            "total_entries": len(entries),
            "date_range": {
                "start": min(dates).isoformat() if dates else datetime.now().isoformat(),
                "end": max(dates).isoformat() if dates else datetime.now().isoformat()
            },
            "sentiment_trend": [
                {"sentiment": k, "count": v} for k, v in sentiment_counts.items()
            ],
            "common_diseases": [
                {"disease": k, "count": v} for k, v in sorted(diseases.items(), key=lambda x: x[1], reverse=True)[:5]
            ],
            "mood_patterns": [
                {"mood": k, "count": v} for k, v in moods.items()
            ],
            "suggestions": suggestions,
            "visualization_data": {
                "time_series": time_series,
                "sentiment_distribution": sentiment_counts
            }
        }
    
    async def query_ncbi_databases(self, condition: str) -> Dict[str, Any]:
        """
        Query multiple NCBI databases (MedGen, PubChem, Gene, PubMed) for comprehensive information.
        Returns aggregated data from all sources.
        """
        ncbi_data = {
            "condition": condition,
            "medgen": None,
            "pubchem": None,
            "gene": None,
            "pubmed": None
        }
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Query MedGen database for medical genetics information
                try:
                    medgen_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                    medgen_params = {
                        "db": "medgen",
                        "term": condition,
                        "retmax": 5,
                        "retmode": "json"
                    }
                    medgen_response = await client.get(medgen_url, params=medgen_params)
                    if medgen_response.status_code == 200:
                        medgen_data = medgen_response.json()
                        if medgen_data.get("esearchresult", {}).get("idlist"):
                            ncbi_data["medgen"] = {
                                "found": True,
                                "result_count": len(medgen_data["esearchresult"]["idlist"]),
                                "ids": medgen_data["esearchresult"]["idlist"][:5]
                            }
                except Exception as e:
                    print(f"Error querying MedGen for {condition}: {e}")
                
                # Query PubChem database for medications/chemicals
                try:
                    pubchem_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                    pubchem_params = {
                        "db": "pccompound",
                        "term": condition,
                        "retmax": 5,
                        "retmode": "json"
                    }
                    pubchem_response = await client.get(pubchem_url, params=pubchem_params)
                    if pubchem_response.status_code == 200:
                        pubchem_data = pubchem_response.json()
                        if pubchem_data.get("esearchresult", {}).get("idlist"):
                            ncbi_data["pubchem"] = {
                                "found": True,
                                "result_count": len(pubchem_data["esearchresult"]["idlist"]),
                                "ids": pubchem_data["esearchresult"]["idlist"][:5]
                            }
                except Exception as e:
                    print(f"Error querying PubChem for {condition}: {e}")
                
                # Query Gene database
                try:
                    gene_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                    gene_params = {
                        "db": "gene",
                        "term": f"{condition}[Gene Name] OR {condition}[Disease]",
                        "retmax": 5,
                        "retmode": "json"
                    }
                    gene_response = await client.get(gene_url, params=gene_params)
                    if gene_response.status_code == 200:
                        gene_data = gene_response.json()
                        if gene_data.get("esearchresult", {}).get("idlist"):
                            ncbi_data["gene"] = {
                                "found": True,
                                "result_count": len(gene_data["esearchresult"]["idlist"]),
                                "ids": gene_data["esearchresult"]["idlist"][:5]
                            }
                except Exception as e:
                    print(f"Error querying Gene database for {condition}: {e}")
                
                # Query PubMed for research articles
                try:
                    pubmed_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                    pubmed_params = {
                        "db": "pubmed",
                        "term": f"{condition} AND (genetic OR genetics OR inherited OR medication OR treatment)",
                        "retmax": 5,
                        "retmode": "json"
                    }
                    pubmed_response = await client.get(pubmed_url, params=pubmed_params)
                    if pubmed_response.status_code == 200:
                        pubmed_data = pubmed_response.json()
                        if pubmed_data.get("esearchresult", {}).get("idlist"):
                            ncbi_data["pubmed"] = {
                                "found": True,
                                "result_count": len(pubmed_data["esearchresult"]["idlist"]),
                                "ids": pubmed_data["esearchresult"]["idlist"][:5]
                            }
                except Exception as e:
                    print(f"Error querying PubMed for {condition}: {e}")
        
        except Exception as e:
            print(f"Error in NCBI database queries: {e}")
        
        return ncbi_data
    
    async def assess_genetic_risk(self, text: str, gender: Optional[str], family_history: Optional[str], azure_clients: AzureClients) -> Optional[str]:
        """
        Assess genetic risk using NCBI API (MedGen, PubChem, Gene, PubMed) and Azure OpenAI.
        Queries all NCBI databases for comprehensive information and uses AI to provide personalized risk assessment.
        """
        if not azure_clients.openai_client:
            return None
        
        try:
            # Extract condition/disease names from text and family history
            conditions = []
            if text:
                conditions.append(text)
            if family_history:
                conditions.append(f"Family history: {family_history}")
            
            combined_text = " ".join(conditions)
            
            # Use Azure OpenAI to extract disease/condition names and medications for NCBI search
            extraction_prompt = f"""Extract the main medical information from this text:
1. Medical conditions, diseases, or genetic disorders
2. Medications or chemical compounds mentioned
3. Symptoms described

Return each item on a separate line. Focus on items that might have genetic or medical database information.

Text: {combined_text}

Return only the extracted items, one per line, nothing else."""
            
            extraction_response = azure_clients.openai_client.chat.completions.create(
                model=azure_clients.openai_deployment,
                messages=[
                    {"role": "system", "content": "You are a medical information extractor. Extract condition names, medications, and symptoms."},
                    {"role": "user", "content": extraction_prompt}
                ],
                temperature=0.3,
                max_tokens=150
            )
            
            items_list = [
                line.strip() 
                for line in extraction_response.choices[0].message.content.strip().split("\n")
                if line.strip() and len(line.strip()) > 2
            ]
            
            if not items_list:
                items_list = [text.split()[0:5]]  # Fallback to first few words
            
            # Query all NCBI databases for each condition/medication
            all_ncbi_data = []
            for item in items_list[:3]:  # Limit to 3 items
                ncbi_data = await self.query_ncbi_databases(item)
                all_ncbi_data.append(ncbi_data)
            
            # Format NCBI data for AI analysis
            ncbi_summary = []
            for data in all_ncbi_data:
                condition = data["condition"]
                sources = []
                
                if data.get("medgen") and data["medgen"].get("found"):
                    sources.append(f"MedGen: {data['medgen']['result_count']} genetic/medical records found")
                if data.get("pubchem") and data["pubchem"].get("found"):
                    sources.append(f"PubChem: {data['pubchem']['result_count']} chemical/medication records found")
                if data.get("gene") and data["gene"].get("found"):
                    sources.append(f"Gene: {data['gene']['result_count']} gene records found")
                if data.get("pubmed") and data["pubmed"].get("found"):
                    sources.append(f"PubMed: {data['pubmed']['result_count']} research articles found")
                
                if sources:
                    ncbi_summary.append(f"- {condition}: {'; '.join(sources)}")
                else:
                    ncbi_summary.append(f"- {condition}: Limited information in NCBI databases")
            
            ncbi_info = "\n".join(ncbi_summary) if ncbi_summary else "No specific information found in NCBI databases (MedGen, PubChem, Gene, PubMed)"
            
            # Use Azure OpenAI to generate personalized risk assessment with comprehensive NCBI data
            risk_assessment_prompt = f"""You are a genetic risk assessment assistant. Based on the following information from multiple NCBI databases, provide a comprehensive, personalized genetic risk assessment.

Patient Information:
- Gender: {gender or 'Not specified'}
- Current Condition/Entry: {text}
- Family History: {family_history or 'None provided'}

NCBI Database Results (MedGen, PubChem, Gene, PubMed):
{ncbi_info}

Use the NCBI data to inform your assessment:
- MedGen: Medical genetics information and genetic conditions
- PubChem: Medication/chemical information and interactions
- Gene: Genetic associations and gene-disease relationships
- PubMed: Latest research on genetic factors and treatments

Provide a comprehensive assessment (3-4 sentences) that:
1. Acknowledges the family history and its potential genetic implications based on MedGen/Gene data
2. Mentions if the condition has known genetic components (from MedGen/Gene databases)
3. References medication information from PubChem if medications are mentioned
4. Incorporates relevant research findings from PubMed
5. Provides gender-specific risk factors if applicable (e.g., breast cancer risk for females with family history)
6. Suggests the importance of genetic counseling or screening if warranted

Be professional, supportive, evidence-based, and reference the NCBI database findings. Do not provide medical diagnosis."""
            
            assessment_response = azure_clients.openai_client.chat.completions.create(
                model=azure_clients.openai_deployment,
                messages=[
                    {"role": "system", "content": "You are a genetic risk assessment assistant. Provide evidence-based, personalized genetic risk information using NCBI database findings (MedGen, PubChem, Gene, PubMed)."},
                    {"role": "user", "content": risk_assessment_prompt}
                ],
                temperature=0.5,
                max_tokens=400
            )
            
            return assessment_response.choices[0].message.content.strip()
            
        except Exception as e:
            print(f"Error in genetic risk assessment: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def _generate_suggestions_with_ncbi(self, entries: List[Dict[str, Any]]) -> List[str]:
        """
        Generate suggestions using NCBI data (MedGen, PubChem, Gene, PubMed) for informed recommendations.
        """
        if not self.azure_clients.openai_client or not entries:
            return []
        
        try:
            recent_entries = entries[-10:] if len(entries) > 10 else entries
            entries_text = "\n".join([
                f"{entry.get('entry_type', 'general')}: {entry.get('text', '')}"
                for entry in recent_entries
            ])
            
            # Extract key conditions, symptoms, medications, and family history from entries
            conditions_and_symptoms = []
            medications = []
            family_history_items = []
            
            for entry in recent_entries:
                text = entry.get('text', '').lower()
                entry_type = entry.get('entry_type', '')
                
                if entry_type == 'family_history':
                    # Family history entries are important for genetic risk assessment
                    family_history_items.append(entry.get('text', ''))
                    # Also add to conditions for NCBI query
                    conditions_and_symptoms.append(entry.get('text', ''))
                elif entry_type in ['chronic_condition', 'genetic_condition', 'past_illness', 'allergy']:
                    conditions_and_symptoms.append(entry.get('text', ''))
                elif entry_type == 'medication':
                    medications.append(entry.get('text', ''))
                elif 'symptom' in text or 'pain' in text or 'ache' in text:
                    conditions_and_symptoms.append(entry.get('text', ''))
            
            # Include family history from entry's family_history field if present
            for entry in recent_entries:
                if entry.get('family_history'):
                    family_history_items.append(entry.get('family_history'))
                    conditions_and_symptoms.append(entry.get('family_history'))
            
            # Query NCBI databases for key conditions/symptoms
            ncbi_data_summary = []
            if conditions_and_symptoms:
                for condition in conditions_and_symptoms[:2]:  # Limit to 2 for performance
                    ncbi_data = await self.query_ncbi_databases(condition)
                    sources_found = []
                    if ncbi_data.get("medgen") and ncbi_data["medgen"].get("found"):
                        sources_found.append(f"MedGen ({ncbi_data['medgen']['result_count']} records)")
                    if ncbi_data.get("gene") and ncbi_data["gene"].get("found"):
                        sources_found.append(f"Gene ({ncbi_data['gene']['result_count']} records)")
                    if ncbi_data.get("pubmed") and ncbi_data["pubmed"].get("found"):
                        sources_found.append(f"PubMed ({ncbi_data['pubmed']['result_count']} articles)")
                    if sources_found:
                        ncbi_data_summary.append(f"{condition}: Found in {', '.join(sources_found)}")
            
            if medications:
                for med in medications[:2]:  # Limit to 2 for performance
                    ncbi_data = await self.query_ncbi_databases(med)
                    if ncbi_data.get("pubchem") and ncbi_data["pubchem"].get("found"):
                        ncbi_data_summary.append(f"{med}: Found in PubChem ({ncbi_data['pubchem']['result_count']} records)")
            
            ncbi_context = "\n".join(ncbi_data_summary) if ncbi_data_summary else "Limited NCBI database information available"
            
            # Include family history information in the prompt
            family_history_context = ""
            if family_history_items:
                family_history_context = f"\n\nFamily History Information:\n" + "\n".join([f"- {fh}" for fh in family_history_items])
            
            # Generate suggestions using NCBI data
            suggestions_prompt = f"""You are a health assistant providing evidence-based suggestions. Use the NCBI database information (MedGen, PubChem, Gene, PubMed) to inform your recommendations.

Patient Diary Entries:
{entries_text}{family_history_context}

NCBI Database Information:
{ncbi_context}

Based on the diary entries and NCBI database findings:
- MedGen: Medical genetics and genetic condition information
- PubChem: Medication and chemical compound data
- Gene: Genetic associations and gene-disease relationships  
- PubMed: Latest research on conditions and treatments

Provide 2-3 gentle, actionable, evidence-based suggestions that:
1. Reference relevant NCBI database findings when applicable
2. Consider genetic factors if family history or genetic conditions are mentioned
3. Account for medication information from PubChem if medications are listed
4. Are supportive, professional, and practical
5. Suggest appropriate medical follow-up if warranted by the data

Format as a simple list. Be specific and reference the NCBI findings when relevant."""
            
            response = self.azure_clients.openai_client.chat.completions.create(
                model=self.azure_clients.openai_deployment,
                messages=[
                    {"role": "system", "content": "You are a health assistant. Provide evidence-based suggestions using NCBI database information (MedGen, PubChem, Gene, PubMed)."},
                    {"role": "user", "content": suggestions_prompt}
                ],
                temperature=0.7,
                max_tokens=300
            )
            
            suggestions_text = response.choices[0].message.content.strip()
            suggestions = [
                s.strip().lstrip("- ").lstrip("* ").lstrip("• ")
                for s in suggestions_text.split("\n")
                if s.strip()
            ]
            return suggestions[:3]
        except Exception as e:
            print(f"Error generating suggestions with NCBI: {e}")
            import traceback
            traceback.print_exc()
            return ["Consider maintaining regular sleep patterns", "Stay hydrated throughout the day"]
    
    def _generate_suggestions(self, entries: List[Dict[str, Any]]) -> List[str]:
        """
        Synchronous wrapper for async suggestions generation.
        Falls back to simple suggestions if async fails.
        """
        if not self.azure_clients.openai_client or not entries:
            return []
        
        try:
            # Try to run async function in sync context
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, schedule as task (but this won't work well in sync context)
                # Fall back to simple suggestions
                return self._generate_simple_suggestions(entries)
            else:
                return loop.run_until_complete(self._generate_suggestions_with_ncbi(entries))
        except:
            return self._generate_simple_suggestions(entries)
    
    async def generate_ai_insights(self, entries: List[Dict[str, Any]], azure_clients: AzureClients) -> Dict[str, Any]:
        """
        Generate AI Health Insights including insights, better life choices, and awareness alerts.
        This is NOT for diagnosis - that's for clinical documentation.
        """
        if not azure_clients.openai_client or not entries:
            return {
                "insights": "Create more diary entries to receive personalized AI health insights.",
                "life_choices": ["Maintain a balanced diet", "Get regular exercise", "Prioritize sleep"],
                "awareness_alerts": []
            }
        
        try:
            # Prepare entry data for analysis
            entries_text = "\n".join([
                f"{entry.get('entry_type', 'general').replace('_', ' ').title()}: {entry.get('text', '')}"
                for entry in entries[-20:]  # Use last 20 entries
            ])
            
            # Collect family history and genetic information
            family_history_items = []
            genetic_conditions = []
            for entry in entries:
                if entry.get('entry_type') == 'family_history':
                    family_history_items.append(entry.get('text', ''))
                elif entry.get('family_history'):
                    family_history_items.append(entry.get('family_history', ''))
                if entry.get('entry_type') == 'genetic_condition':
                    genetic_conditions.append(entry.get('text', ''))
            
            family_context = ""
            if family_history_items:
                family_context = f"\n\nFamily History:\n" + "\n".join([f"- {fh}" for fh in family_history_items])
            
            # Query NCBI for key conditions mentioned
            conditions_to_query = []
            for entry in entries[-10:]:
                if entry.get('entry_type') in ['chronic_condition', 'genetic_condition', 'past_illness']:
                    conditions_to_query.append(entry.get('text', ''))
            
            ncbi_summary = []
            if conditions_to_query:
                for condition in conditions_to_query[:3]:  # Limit to 3
                    ncbi_data = await self.query_ncbi_databases(condition)
                    sources = []
                    if ncbi_data.get("medgen") and ncbi_data["medgen"].get("found"):
                        sources.append(f"MedGen ({ncbi_data['medgen']['result_count']} records)")
                    if ncbi_data.get("pubchem") and ncbi_data["pubchem"].get("found"):
                        sources.append(f"PubChem ({ncbi_data['pubchem']['result_count']} records)")
                    if ncbi_data.get("gene") and ncbi_data["gene"].get("found"):
                        sources.append(f"Gene ({ncbi_data['gene']['result_count']} records)")
                    if ncbi_data.get("pubmed") and ncbi_data["pubmed"].get("found"):
                        sources.append(f"PubMed ({ncbi_data['pubmed']['result_count']} articles)")
                    if sources:
                        ncbi_summary.append(f"{condition}: Found in {', '.join(sources)}")
            
            ncbi_context = "\n".join(ncbi_summary) if ncbi_summary else "Limited NCBI database information available"
            
            # Generate comprehensive AI insights
            insights_prompt = f"""You are an AI health insights assistant. Analyze the following health diary entries and provide comprehensive insights. IMPORTANT: Do NOT provide medical diagnoses - that is for clinical documentation only.

Patient Health Diary Entries:
{entries_text}{family_context}

NCBI Database Information (MedGen, PubChem, Gene, PubMed):
{ncbi_context}

Provide three types of insights:

1. AI Health Insights (1-2 short paragraphs, keep it concise):
   - Overall health patterns and trends you observe
   - Connections between different entries (e.g., family history and current conditions)
   - Positive health indicators
   - Areas showing improvement or concern
   - Reference NCBI database findings when relevant
   - Be supportive, encouraging, and evidence-based
   - DO NOT diagnose - focus on patterns and observations
   - IMPORTANT: Keep insights brief and to the point (about half the length of typical analysis)

2. Better Life Choices (3-5 actionable recommendations):
   - Specific, practical lifestyle improvements
   - Based on the diary entries and NCBI data
   - Consider genetic risk factors if family history is present
   - Focus on prevention and wellness
   - Format as a simple list

3. Things to Be Aware Of (2-4 items):
   - Potential risk factors or patterns to monitor
   - Based on entries, family history, and NCBI findings
   - NOT diagnoses - just things to be aware of and discuss with healthcare providers
   - Include when to consider consulting a healthcare professional
   - Format as a simple list

Remember: This is for health awareness and lifestyle guidance, NOT medical diagnosis."""

            response = azure_clients.openai_client.chat.completions.create(
                model=azure_clients.openai_deployment,
                messages=[
                    {"role": "system", "content": "You are an AI health insights assistant. Provide health awareness, lifestyle guidance, and things to be aware of. NEVER provide medical diagnoses - that is for clinical documentation only."},
                    {"role": "user", "content": insights_prompt}
                ],
                temperature=0.7,
                max_tokens=500
            )
            
            insights_text = response.choices[0].message.content.strip()
            
            # Parse the response to extract the three sections
            life_choices = []
            awareness_alerts = []
            insights_paragraph = insights_text
            
            # Try to extract structured sections
            if "Better Life Choices" in insights_text or "life choices" in insights_text.lower():
                parts = insights_text.split("Better Life Choices")
                if len(parts) > 1:
                    insights_paragraph = parts[0].strip()
                    remaining = parts[1]
                    if "Things to Be Aware Of" in remaining:
                        choices_part = remaining.split("Things to Be Aware Of")[0]
                        awareness_part = remaining.split("Things to Be Aware Of")[1]
                    else:
                        choices_part = remaining
                        awareness_part = ""
                    
                    # Extract list items
                    for line in choices_part.split("\n"):
                        line = line.strip().lstrip("- ").lstrip("* ").lstrip("• ").lstrip("1.").lstrip("2.").lstrip("3.").lstrip("4.").lstrip("5.")
                        if line and len(line) > 10:
                            life_choices.append(line)
                    
                    for line in awareness_part.split("\n"):
                        line = line.strip().lstrip("- ").lstrip("* ").lstrip("• ").lstrip("1.").lstrip("2.").lstrip("3.").lstrip("4.")
                        if line and len(line) > 10:
                            awareness_alerts.append(line)
            
            # Fallback if parsing didn't work well
            if not life_choices:
                life_choices = ["Maintain regular health check-ups", "Follow medication schedules as prescribed", "Stay active and maintain a balanced diet"]
            if not awareness_alerts:
                awareness_alerts = ["Monitor any persistent symptoms", "Discuss family history with healthcare providers"]
            
            # Clean up insights paragraph - remove markdown formatting
            insights_paragraph = insights_paragraph.split("AI Health Insights")[-1].strip()
            # Remove all markdown bold formatting (**text**)
            insights_paragraph = re.sub(r'\*\*([^*]+)\*\*', r'\1', insights_paragraph)
            # Remove any remaining single asterisks
            insights_paragraph = insights_paragraph.replace('**', '').strip()
            if not insights_paragraph or len(insights_paragraph) < 50:
                insights_paragraph = "Based on your health diary entries, I've analyzed your health patterns. Continue tracking your health data for more personalized insights."
            
            return {
                "insights": insights_paragraph,
                "life_choices": life_choices[:5],
                "awareness_alerts": awareness_alerts[:4]
            }
            
        except Exception as e:
            print(f"Error generating AI insights: {e}")
            import traceback
            traceback.print_exc()
            return {
                "insights": "Unable to generate insights at this time. Please try again later.",
                "life_choices": ["Maintain regular health check-ups", "Follow medication schedules", "Stay active"],
                "awareness_alerts": []
            }
    
    def _generate_simple_suggestions(self, entries: List[Dict[str, Any]]) -> List[str]:
        """Fallback simple suggestions without NCBI data."""
        try:
            recent_entries = entries[-10:] if len(entries) > 10 else entries
            entries_text = "\n".join([
                f"{entry.get('entry_type', 'general')}: {entry.get('text', '')}"
                for entry in recent_entries
            ])
            
            response = self.azure_clients.openai_client.chat.completions.create(
                model=self.azure_clients.openai_deployment,
                messages=[
                    {"role": "system", "content": "You are a health assistant. Provide 2-3 gentle, actionable suggestions based on health diary entries. Be supportive and professional. Format as a simple list."},
                    {"role": "user", "content": f"Based on these diary entries, provide suggestions:\n{entries_text}"}
                ],
                temperature=0.7,
                max_tokens=200
            )
            
            suggestions_text = response.choices[0].message.content.strip()
            suggestions = [
                s.strip().lstrip("- ").lstrip("* ")
                for s in suggestions_text.split("\n")
                if s.strip()
            ]
            return suggestions[:3]
        except:
            return ["Consider maintaining regular sleep patterns", "Stay hydrated throughout the day"]


class SOAPPipeline:
    
    def __init__(self, azure_clients: AzureClients):
        self.azure_clients = azure_clients
    
    def generate_soap_note(self, transcription: str, health_entities: Optional[Dict] = None, diary_entries: Optional[List[Dict]] = None) -> Dict[str, str]:
        if not self.azure_clients.openai_client:
            print("WARNING: OpenAI client not available, using fallback SOAP generation")
            return self._generate_fallback_soap(transcription, health_entities)
        
        try:
            context = transcription
            entities_context = ""
            if health_entities and health_entities.get("entities"):
                entities_list = []
                for e in health_entities["entities"][:15]:
                    entities_list.append(f"- {e['text']} (Category: {e['category']}, Confidence: {e['confidence']:.2f})")
                entities_context = "\n\nExtracted Medical Entities from Text Analytics:\n" + "\n".join(entities_list)
                context += entities_context
            
            diary_context = ""
            if diary_entries and len(diary_entries) > 0:
                relevant_entries = []
                for entry in diary_entries:
                    if entry.get("entry_type") in ["chronic_condition", "genetic_condition", "past_illness", "medication"]:
                        entry_date = entry.get("timestamp", "")
                        entry_text = entry.get("text", "")
                        entry_type = entry.get("entry_type", "")
                        relevant_entries.append(f"- {entry_type.upper()}: {entry_text} (Logged: {entry_date})")
                
                if relevant_entries:
                    diary_context = "\n\n=== PATIENT HEALTH DIARY ENTRIES (MEDICAL HISTORY) ===\n" + "\n".join(relevant_entries) + "\n=== END DIARY ENTRIES ===\n"
                    context += diary_context
                    print(f"Including {len(relevant_entries)} diary entries in SOAP context:")
                    for entry in relevant_entries:
                        print(f"  - {entry}")
            
            system_prompt = """You are a clinical documentation assistant. Your role is to create professional SOAP notes in standard clinical format.

CRITICAL RULES:
1. ONLY use information explicitly mentioned in the input. DO NOT add details that were not provided.
2. Write as a clinical document, not a conversation. Use third person, objective medical language.
3. Do NOT use "you", "you should", "you mentioned", or any direct address to the patient.
4. Use concise, professional clinical phrasing. Avoid long paragraphs.
5. Format your response EXACTLY as follows with clear section headers:

===SUBJECTIVE===
[Content here]

===OBJECTIVE===
[Content here]

===ASSESSMENT===
[Content here]

===PLAN===
[Content here]"""

            diary_instruction = ""
            if diary_context:
                diary_instruction = "\n\nCRITICAL: The patient has logged health diary entries above showing their medical history. You MUST reference these entries in your SOAP note:\n\n1. SUBJECTIVE section: Include ALL diseases/conditions and medications from diary entries in the medical history. For example: 'Past medical history: Diabetes type 3 (per patient diary). Current medications: [list from diary].'\n\n2. ASSESSMENT section: You MUST consider existing conditions from diary when making diagnoses. If patient has diabetes type 3, this significantly affects assessment. State: 'Primary: [diagnosis]. Patient's history of [disease from diary] is relevant as [explanation].'\n\n3. PLAN section: Account for existing medications and conditions. Check for interactions, contraindications, or necessary adjustments based on diary entries.\n\nDO NOT ignore diary entries. They are part of the patient's documented medical history and must be included."
            
            user_prompt = f"""Create a clinical SOAP note from this patient dictation. Write as a professional medical document.

Patient dictation:
{context}
{diary_instruction}

IMPORTANT: The diary entries shown above are PART OF THE PATIENT'S MEDICAL RECORD. You MUST include them in your SOAP note. They are not optional - they are documented medical history.

Generate a SOAP note in clinical format:

===SUBJECTIVE===
Document what the patient reported AND their medical history from diary entries:
- Chief complaint in patient's words
- History of present illness: symptoms, timing, severity, location (from dictation)
- Past medical history: MUST include ALL diseases/conditions from diary entries (e.g., "Past medical history: Diabetes type 3 per patient diary")
- Current medications: MUST include ALL medications from diary entries
- Write in third person, concise clinical language
- Example: "Patient reports [symptom]. Past medical history: [list ALL diseases from diary]. Current medications: [list ALL medications from diary]. Denies [if mentioned]."

===OBJECTIVE===
Document only measurable or observable findings:
- Vital signs if mentioned (BP, HR, RR, Temp, O2 sat)
- Physical examination findings if described
- Test results, lab values, or imaging if mentioned
- If no objective findings were provided, state: "No objective findings documented."
- Use third person, objective clinical language
- Keep it concise and factual

===ASSESSMENT===
Provide differential diagnoses with clinical reasoning:
- Most likely diagnosis based on symptom pattern AND existing conditions from diary
- 2-4 differential diagnoses ranked by likelihood
- Brief clinical reasoning for each
- MANDATORY: You MUST reference diseases/conditions from diary entries in your assessment
- If patient has diabetes type 3 in diary, you MUST state how this affects the current presentation
- Example: "Primary: Hyperglycemia. Patient's documented history of Diabetes type 3 (per diary) is highly relevant as this condition directly relates to blood sugar dysregulation. The headache may be secondary to hyperglycemia given this history."
- Use medical terminology and standard diagnostic criteria
- Format as concise clinical text, not long paragraphs

===PLAN===
Document clear clinical management steps:
- Medications with dosages if appropriate
- Consider existing medications from diary entries - check for interactions or adjustments needed
- Diagnostic tests to order
- Follow-up recommendations
- Patient education points
- Write as medical steps, not advice or conversation
- Use concise clinical phrasing
- Format with each numbered item on a separate line
- Example format:
1. [Medication] [dose] [frequency]
2. Order [test]
3. Follow-up in [timeframe]
4. [Additional step]

Remember: Write as a clinical document. Use third person. Be concise and professional. Reference diary entries for medical history, existing conditions, and medications."""

            print(f"Calling Azure OpenAI with transcription: {transcription[:100]}...")
            print(f"OpenAI client available: {self.azure_clients.openai_client is not None}")
            
            response = self.azure_clients.openai_client.chat.completions.create(
                model=self.azure_clients.openai_deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.4,
                max_tokens=2000
            )
            
            soap_text = response.choices[0].message.content.strip()
            print(f"AI Response received (length: {len(soap_text)}): {soap_text[:200]}...")
            
            soap_note = self._parse_soap_response(soap_text, transcription)
            print(f"Parsed SOAP note - Subjective: {len(soap_note.get('subjective', ''))} chars, Assessment: {len(soap_note.get('assessment', ''))} chars")
            
            if not soap_note.get("assessment") or "pending" in soap_note.get("assessment", "").lower() or "to be" in soap_note.get("assessment", "").lower():
                print("WARNING: AI generated placeholder text, trying again with more explicit instructions")
                return self._retry_soap_generation(transcription, health_entities, diary_entries)
            
            return soap_note
        except Exception as e:
            print(f"Error generating SOAP note: {e}")
            import traceback
            traceback.print_exc()
            return self._generate_fallback_soap(transcription, health_entities)
    
    def update_soap_incremental(self, new_text_chunk: str, current_soap: Dict[str, str], full_transcript: str, diary_entries: Optional[List[Dict]] = None) -> Dict[str, str]:
        if not self.azure_clients.openai_client:
            return current_soap
        
        try:
            diary_context = ""
            if diary_entries and len(diary_entries) > 0:
                relevant_entries = []
                for entry in diary_entries:
                    if entry.get("entry_type") in ["chronic_condition", "genetic_condition", "past_illness", "medication"]:
                        entry_date = entry.get("timestamp", "")
                        entry_text = entry.get("text", "")
                        entry_type = entry.get("entry_type", "")
                        relevant_entries.append(f"- {entry_type.upper()}: {entry_text} (Logged: {entry_date})")
                
                if relevant_entries:
                    diary_context = "\n\n=== PATIENT HEALTH DIARY ENTRIES (MEDICAL HISTORY) ===\n" + "\n".join(relevant_entries) + "\n=== END DIARY ENTRIES ===\n"
            
            has_subjective = bool(current_soap.get('subjective', '').strip())
            has_assessment = bool(current_soap.get('assessment', '').strip())
            has_plan = bool(current_soap.get('plan', '').strip())
            
            priority_instruction = ""
            if not has_subjective:
                priority_instruction = "\n\nPRIORITY: Generate Subjective section FIRST. Extract the chief complaint and initial symptoms from the transcript immediately."
            elif not has_assessment:
                priority_instruction = "\n\nPRIORITY: Generate Assessment section next. Provide an early rough hypothesis based on current symptoms. It can be refined later."
            elif not has_plan:
                priority_instruction = "\n\nPRIORITY: Generate Plan section. Assessment should already exist."
            
            update_prompt = f"""You are updating a clinical SOAP note incrementally during live transcription. You have the current SOAP note state and transcript.

Current SOAP Note State:
Subjective: {current_soap.get('subjective', '')}
Objective: {current_soap.get('objective', 'No objective findings documented.')}
Assessment: {current_soap.get('assessment', '')}
Plan: {current_soap.get('plan', '')}

Full transcript so far: {full_transcript}
New text chunk to incorporate: {new_text_chunk}
{diary_context}
{priority_instruction}

Your task: Update the SOAP note by incorporating the new information. Follow these priorities:
1. SUBJECTIVE must appear FIRST - extract chief complaint and symptoms immediately
2. ASSESSMENT appears next - provide early rough hypothesis that can refine over time
3. PLAN appears later - only after assessment is established
4. OBJECTIVE - document only if mentioned, otherwise keep "No objective findings documented"

Rules:
- If Subjective is empty, generate it NOW from the transcript
- If Assessment is empty but Subjective exists, generate an early hypothesis
- If Plan is empty but Assessment exists, generate a basic plan
- Merge new information into existing sections
- Keep existing content that is still valid
- Maintain clinical format and third-person language
- Reference diary entries if relevant

Return the updated SOAP note in this exact format:

===SUBJECTIVE===
[Updated subjective section]

===OBJECTIVE===
[Updated objective section]

===ASSESSMENT===
[Updated assessment section]

===PLAN===
[Updated plan section]"""

            response = self.azure_clients.openai_client.chat.completions.create(
                model=self.azure_clients.openai_deployment,
                messages=[
                    {"role": "system", "content": "You are a clinical documentation assistant. Update SOAP notes incrementally by merging new information into existing sections."},
                    {"role": "user", "content": update_prompt}
                ],
                temperature=0.3,
                max_tokens=2000
            )
            
            soap_text = response.choices[0].message.content.strip()
            updated_soap = self._parse_soap_response(soap_text, full_transcript)
            
            return updated_soap
        except Exception as e:
            print(f"Error in incremental SOAP update: {e}")
            return current_soap
    
    def _generate_fallback_soap(self, transcription: str, health_entities: Optional[Dict] = None) -> Dict[str, str]:
        print("WARNING: Using rule-based fallback. OpenAI client should be configured for dynamic AI analysis.")
        transcription_lower = transcription.lower()
        
        symptoms_found = []
        if health_entities and health_entities.get("entities"):
            symptoms_found = [e['text'] for e in health_entities["entities"] if e.get('category') in ['Symptom', 'Condition', 'Diagnosis', 'BodyStructure']]
        
        has_fever = "fever" in transcription_lower or "temperature" in transcription_lower or "hot" in transcription_lower
        has_pain = "pain" in transcription_lower or "hurts" in transcription_lower or "ache" in transcription_lower or "sore" in transcription_lower
        has_swelling = "swelling" in transcription_lower or "swollen" in transcription_lower
        has_cough = "cough" in transcription_lower
        has_headache = "headache" in transcription_lower or "head" in transcription_lower and "ache" in transcription_lower
        has_nausea = "nausea" in transcription_lower or "nauseous" in transcription_lower
        has_diarrhea = "diarrhea" in transcription_lower or "diarrhoea" in transcription_lower
        has_rash = "rash" in transcription_lower
        neck_involved = "neck" in transcription_lower
        chest_involved = "chest" in transcription_lower or "breast" in transcription_lower
        abdominal_involved = "stomach" in transcription_lower or "abdomen" in transcription_lower or "belly" in transcription_lower
        facial_involved = "cheek" in transcription_lower or "face" in transcription_lower or "jaw" in transcription_lower
        
        subjective = f"Chief Complaint: {transcription}\nHistory of Present Illness: Patient reports {transcription.lower()}"
        
        objective_parts = []
        if has_fever:
            objective_parts.append("Temperature measurement indicated")
        if has_pain:
            objective_parts.append("Pain assessment and location-specific examination")
        if has_swelling:
            objective_parts.append("Examination of affected area for swelling, erythema, warmth")
        if neck_involved:
            objective_parts.append("Neck examination and lymph node palpation")
        if facial_involved:
            objective_parts.append("Facial and parotid gland examination")
        if chest_involved:
            objective_parts.append("Chest auscultation and respiratory assessment")
        if abdominal_involved:
            objective_parts.append("Abdominal examination and palpation")
        if has_cough:
            objective_parts.append("Respiratory examination and lung auscultation")
        if has_rash:
            objective_parts.append("Skin examination and rash characterization")
        
        objective = "Vital signs assessment. " + ". ".join(objective_parts) + ". General physical examination." if objective_parts else "Complete physical examination and vital signs assessment."
        
        assessment_parts = []
        if has_swelling and facial_involved and has_fever:
            assessment_parts.append("Primary Diagnosis: Mumps, parotitis, or sialadenitis")
            assessment_parts.append("Differential Diagnoses: 1) Lymphadenitis 2) Viral parotitis 3) Bacterial sialadenitis")
            assessment_parts.append("Clinical Reasoning: Bilateral or unilateral facial swelling with fever and neck involvement suggests infectious process affecting salivary glands or lymph nodes")
        elif has_fever and neck_involved and has_pain:
            assessment_parts.append("Primary Diagnosis: Cervical lymphadenitis or upper respiratory infection")
            assessment_parts.append("Differential Diagnoses: 1) Viral infection (EBV, CMV) 2) Bacterial lymphadenitis 3) Inflammatory condition")
            assessment_parts.append("Clinical Reasoning: Fever with neck pain and possible lymph node involvement indicates infectious or inflammatory process")
        elif has_headache and has_nausea:
            assessment_parts.append("Primary Diagnosis: Migraine or tension headache")
            assessment_parts.append("Differential Diagnoses: 1) Tension headache 2) Viral syndrome 3) Intracranial pathology (less likely)")
            assessment_parts.append("Clinical Reasoning: Headache with nausea is classic migraine presentation, though other causes should be considered")
        elif has_cough and has_fever:
            assessment_parts.append("Primary Diagnosis: Upper respiratory infection or pneumonia")
            assessment_parts.append("Differential Diagnoses: 1) Viral URI 2) Bacterial pneumonia 3) Bronchitis")
            assessment_parts.append("Clinical Reasoning: Cough with fever suggests respiratory tract infection")
        elif has_diarrhea and has_fever:
            assessment_parts.append("Primary Diagnosis: Gastroenteritis")
            assessment_parts.append("Differential Diagnoses: 1) Viral gastroenteritis 2) Bacterial infection 3) Food poisoning")
            assessment_parts.append("Clinical Reasoning: Diarrhea with fever indicates gastrointestinal infection")
        elif has_rash and has_fever:
            assessment_parts.append("Primary Diagnosis: Viral exanthem or drug reaction")
            assessment_parts.append("Differential Diagnoses: 1) Viral rash (measles, rubella, etc.) 2) Drug reaction 3) Allergic reaction")
            assessment_parts.append("Clinical Reasoning: Fever with rash suggests viral illness or hypersensitivity reaction")
        else:
            symptom_list = []
            if has_fever: symptom_list.append("fever")
            if has_pain: symptom_list.append("pain")
            if has_swelling: symptom_list.append("swelling")
            if has_cough: symptom_list.append("cough")
            if has_headache: symptom_list.append("headache")
            if has_nausea: symptom_list.append("nausea")
            if symptoms_found:
                symptom_list.extend([s for s in symptoms_found[:3] if s not in symptom_list])
            
            assessment_parts.append(f"Primary Diagnosis: Clinical assessment based on symptom pattern ({', '.join(symptom_list[:4])})")
            assessment_parts.append("Differential Diagnoses: Further evaluation needed to narrow differential")
            assessment_parts.append("Clinical Reasoning: Symptom constellation requires comprehensive evaluation")
        
        assessment = ". ".join(assessment_parts)
        
        plan_items = []
        if has_fever:
            plan_items.append("Antipyretic: Acetaminophen 500-1000mg q6h or Ibuprofen 400-600mg q6h for fever")
        if has_pain:
            plan_items.append("Analgesia as needed for pain management")
        if has_swelling and facial_involved:
            plan_items.append("Warm compresses to affected area")
            plan_items.append("Consider viral serology (mumps, EBV) and CBC with differential")
        elif has_fever and neck_involved:
            plan_items.append("CBC, inflammatory markers (ESR, CRP), and consider imaging if abscess suspected")
        elif has_headache:
            plan_items.append("Headache management with appropriate analgesics")
            plan_items.append("Consider neuroimaging if red flag symptoms present")
        elif has_cough:
            plan_items.append("Chest X-ray if pneumonia suspected, symptomatic treatment")
        elif has_diarrhea:
            plan_items.append("Stool studies if indicated, hydration, anti-diarrheal if appropriate")
        else:
            plan_items.append("Symptomatic treatment based on specific symptoms")
            plan_items.append("Diagnostic workup as clinically indicated")
        
        plan_items.append("Follow-up in 3-5 days or sooner if symptoms worsen")
        plan_items.append("Patient education on symptom management and when to seek immediate care")
        
        plan = "1. " + " 2. ".join(plan_items)
        
        return {
            "subjective": subjective,
            "objective": objective,
            "assessment": assessment,
            "plan": plan
        }
    
    def _retry_soap_generation(self, transcription: str, health_entities: Optional[Dict] = None, diary_entries: Optional[List[Dict]] = None) -> Dict[str, str]:
        try:
            context = transcription
            if health_entities and health_entities.get("entities"):
                entities_text = ", ".join([e['text'] for e in health_entities["entities"][:10]])
                context += f"\n\nMedical entities found: {entities_text}"
            
            if diary_entries and len(diary_entries) > 0:
                relevant_entries = []
                for entry in diary_entries:
                    if entry.get("entry_type") in ["chronic_condition", "genetic_condition", "past_illness", "medication"]:
                        entry_date = entry.get("timestamp", "")
                        entry_text = entry.get("text", "")
                        entry_type = entry.get("entry_type", "")
                        relevant_entries.append(f"- {entry_type.upper()}: {entry_text} (Logged: {entry_date})")
                
                if relevant_entries:
                    context += "\n\nPatient Health Diary Entries (RELEVANT MEDICAL HISTORY):\n" + "\n".join(relevant_entries)
            
            retry_prompt = f"""Create a clinical SOAP note from this patient dictation. Write as a professional medical document in third person. Do not use "you" or conversational language. Reference any diseases/medications from diary entries in your assessment and plan.

Patient dictation: {context}

Format your response EXACTLY as:

===SUBJECTIVE===
[Only what patient reported - third person, clinical language]

===OBJECTIVE===
[Only measurable/observable findings, or "No objective findings documented" if none]

===ASSESSMENT===
[Differential diagnoses with reasoning - concise clinical text]

===PLAN===
[Clinical management steps - medical phrasing, not advice. Format with each numbered item on a separate line:
1. First step
2. Second step
3. Third step]

Write as a clinical document. Use third person. Be concise. Only use information actually mentioned."""

            response = self.azure_clients.openai_client.chat.completions.create(
                model=self.azure_clients.openai_deployment,
                messages=[
                    {"role": "system", "content": "You are a medical assistant. Generate complete SOAP notes with real diagnoses and treatment plans. Never use placeholder text."},
                    {"role": "user", "content": retry_prompt}
                ],
                temperature=0.5,
                max_tokens=2000
            )
            
            soap_text = response.choices[0].message.content.strip()
            return self._parse_soap_response(soap_text, transcription)
        except:
            return self._generate_fallback_soap(transcription, health_entities)
    
    def _parse_soap_response(self, soap_text: str, transcription: str = "") -> Dict[str, str]:
        sections = {
            "subjective": "",
            "objective": "",
            "assessment": "",
            "plan": ""
        }
        
        text_lower = soap_text.lower()
        
        section_markers = {
            "subjective": ["===subjective===", "subjective:", "**subjective**", "subjective (s):", "s:"],
            "objective": ["===objective===", "objective:", "**objective**", "objective (o):", "o:"],
            "assessment": ["===assessment===", "assessment:", "**assessment**", "assessment (a):", "a:", "impression:", "diagnosis:"],
            "plan": ["===plan===", "plan:", "**plan**", "plan (p):", "p:", "treatment plan:"]
        }
        
        section_keywords = {
            "subjective": ["subjective", "chief complaint", "history of present illness", "hpi"],
            "objective": ["objective", "physical examination", "vital signs", "exam", "objective findings"],
            "assessment": ["assessment", "impression", "diagnosis", "clinical assessment", "differential diagnosis", "primary diagnosis"],
            "plan": ["plan", "treatment", "follow-up", "management", "treatment plan"]
        }
        
        lines = soap_text.split("\n")
        current_section = None
        collecting = False
        
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                if collecting and current_section:
                    sections[current_section] += "\n"
                continue
                
            line_lower = line_stripped.lower()
            
            section_found = False
            for section, markers in section_markers.items():
                for marker in markers:
                    if line_lower.startswith(marker):
                        current_section = section
                        collecting = True
                        section_found = True
                        remaining = line_stripped[len(marker):].strip().lstrip(":").strip().lstrip("-").strip()
                        if remaining:
                            sections[current_section] = remaining
                        else:
                            sections[current_section] = ""
                        break
                if section_found:
                    break
            
            if not section_found and collecting and current_section:
                next_section_marker = False
                for other_section, other_markers in section_markers.items():
                    if other_section != current_section:
                        for marker in other_markers:
                            if line_lower.startswith(marker):
                                next_section_marker = True
                                break
                        if next_section_marker:
                            break
                
                if not next_section_marker:
                    if sections[current_section] and not sections[current_section].endswith("\n"):
                        sections[current_section] += "\n"
                    sections[current_section] += line_stripped
        
        if not any(sections.values()):
            for section, keywords in section_keywords.items():
                for kw in keywords:
                    if kw in text_lower:
                        idx = text_lower.find(kw)
                        if idx != -1:
                            start_idx = idx + len(kw)
                            next_section_idx = len(soap_text)
                            for other_section, other_keywords in section_keywords.items():
                                if other_section != section:
                                    for other_kw in other_keywords:
                                        other_idx = text_lower.find(other_kw, start_idx)
                                        if other_idx != -1 and other_idx < next_section_idx:
                                            next_section_idx = other_idx
                            sections[section] = soap_text[start_idx:next_section_idx].strip().lstrip(":").strip()
                            break
                    if sections[section]:
                        break
        
        if not any(sections.values()):
            paragraphs = [p.strip() for p in soap_text.split("\n\n") if p.strip()]
            if len(paragraphs) >= 4:
                sections["subjective"] = paragraphs[0]
                sections["objective"] = paragraphs[1]
                sections["assessment"] = paragraphs[2]
                sections["plan"] = paragraphs[3]
            elif len(paragraphs) > 0:
                sections["subjective"] = paragraphs[0]
                if len(paragraphs) > 1:
                    sections["objective"] = paragraphs[1]
                if len(paragraphs) > 2:
                    sections["assessment"] = paragraphs[2]
                if len(paragraphs) > 3:
                    sections["plan"] = paragraphs[3]
            else:
                sections["subjective"] = soap_text
        
        for section in sections:
            sections[section] = sections[section].strip()
            if not sections[section]:
                if section == "subjective" and transcription:
                    sections[section] = transcription
                else:
                    sections[section] = f"{section.capitalize()} information to be documented."
        
        if not sections["subjective"] or sections["subjective"] == "Subjective information to be documented.":
            sections["subjective"] = transcription if transcription else "Patient symptoms and complaints to be documented."
        
        return sections
