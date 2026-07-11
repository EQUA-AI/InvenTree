# Azure Resource Inventory & Monthly Cost Breakdown

**Generated:** December 16, 2025  
**Resource Group:** EpconChat  
**Region:** East US 2

---

## 📦 Resources in EpconChat Resource Group

| Resource | Type | SKU/Tier | Location |
|----------|------|----------|----------|
| **AIMMS-Foundry** | AI Services (Azure AI Foundry) | S0 | East US 2 |
| **EquaAgentDocIntelligence** | Document Intelligence (Form Recognizer) | S0 | East US 2 |
| **epcon-ai** | Container App | Consumption (0.5 vCPU, 1GB RAM) | East US 2 |
| **inventree-worker** | Container App | Consumption (0.5 vCPU, 1GB RAM) | East US 2 |
| **epcon-ai-env** | Container Apps Environment | - | East US 2 |
| **aimms** | Container Registry | Standard | East US 2 |
| **epcon0ai0storage** | Storage Account | Standard_RAGRS (Hot) | East US 2 |
| **epcon-key-vault** | Key Vault | Standard | East US 2 |
| **machine-ai-chat** | PostgreSQL Flexible Server | Standard_B1ms (Burstable), 32GB | East US 2 |
| **EquaAIid** | Azure AD B2C/CIAM Directory | Base (A0) | United States |

---

## 🔗 External Resources (Different Resource Groups)

| Resource | Type | SKU | Resource Group | Location |
|----------|------|-----|----------------|----------|
| **dev-search-2wfnxagc7mn5g** | Azure AI Search | Standard | rg-wedding-onmi-ai | East US 2 |

---

## 🔧 Resource Configuration Details

### Azure AI Foundry (AIMMS-Foundry)
- **Endpoint:** `https://aimms-foundry.openai.azure.com/`
- **Project:** Epcon-AIMMS
- **Deployments:**
  - `gpt-5.2` (Primary model)
  - `text-embedding-3-large` (Embeddings)
- **Capabilities:** OpenAI, Speech, Document Intelligence, Content Safety, Translation

### Document Intelligence (EquaAgentDocIntelligence)
- **Endpoint:** `https://equaagentdocintelligence.cognitiveservices.azure.com/`
- **Capabilities:** Form Recognition, Document Analysis, Custom Models

### Azure AI Search
- **Endpoint:** `https://dev-search-2wfnxagc7mn5g.search.windows.net`
- **Index:** `aimms-memory`
- **Purpose:** Long-term memory store for AIMMS

### Container Apps
- **epcon-ai:** Main InvenTree application
  - Custom domain: `aimms.equa.work`
  - Min replicas: 1, Max replicas: 10
  - Image: `aimms-hjcxb6epgvhgbyge.azurecr.io/inventree:kanban-board`
- **inventree-worker:** Background task worker
  - Min replicas: 1, Max replicas: 1
  - Internal ingress only

### PostgreSQL Flexible Server
- **Host:** `machine-ai-chat.postgres.database.azure.com`
- **Version:** PostgreSQL 17
- **SKU:** Standard_B1ms (Burstable)
- **Storage:** 32GB (P4 tier)
- **Backup Retention:** 7 days

### Storage Account (epcon0ai0storage)
- **Type:** Standard_RAGRS (Geo-redundant)
- **Access Tier:** Hot
- **Primary:** East US 2
- **Secondary:** Central US
- **Large File Shares:** Enabled

---

## 🏗️ Baseline Infrastructure Cost (No Token Usage)

These are fixed monthly costs incurred regardless of AI/OpenAI usage:

| Resource | SKU/Tier | Monthly Cost |
|----------|----------|--------------|
| **Azure AI Search** | Standard | $250.00 |
| **Container App (epcon-ai)** | Consumption (0.5 vCPU, 1GB) | $35.00 |
| **Container App (worker)** | Consumption (0.5 vCPU, 1GB) | $35.00 |
| **PostgreSQL Flexible Server** | B1ms + 32GB storage | $35.00 |
| **Container Registry** | Standard | $20.00 |
| **Storage Account** | Standard_RAGRS | $5.00 |
| **Key Vault** | Standard | $1.00 |
| **Azure AD B2C/CIAM** | Base (first 50K MAU free) | $0.00 |
| **AI Foundry (AIMMS-Foundry)** | S0 (pay-per-use, no base fee) | $0.00 |
| **Document Intelligence** | S0 (pay-per-use, no base fee) | $0.00 |
| **TOTAL BASELINE** | | **~$381/month** |

> 💡 **Note:** This is your minimum monthly spend even with zero AI queries. The biggest fixed cost is **Azure AI Search ($250)** which accounts for 66% of baseline costs.

---

## 💰 Estimated Monthly Cost Breakdown

### 🤖 Azure OpenAI Token Usage (2-5 Users)

**Pricing (GPT-5.2 - Azure OpenAI Global Standard):**
- Input tokens: **$1.75 per 1M tokens** ($0.00175 per 1K)
- Cached Input tokens: **$0.175 per 1M tokens** ($0.000175 per 1K)
- Output tokens: **$14.00 per 1M tokens** ($0.014 per 1K)
- Embeddings (text-embedding-3-large): ~$0.13 per 1M tokens

| Usage Level | Queries/User/Day | Tokens/Month | Est. Cost/Month |
|-------------|------------------|--------------|-----------------||
| **Low** (Light/Casual) | 10-20 queries | 1.5M - 3M | **$15 - $35** |
| **Medium** (Regular Business) | 50-100 queries | 7.5M - 15M | **$70 - $150** |
| **High** (Power Users/Automation) | 200-400 queries | 30M - 60M | **$280 - $600** |

**Usage Assumptions:**
- Low: Quick lookups, occasional questions (~500 input + 800 output tokens/query)
- Medium: Daily workflows, document analysis (~1,000 input + 1,500 output tokens/query)
- High: Heavy automation, batch processing, complex reasoning (~1,500 input + 2,500 output tokens/query)

**Embeddings Add-on (for semantic search/memory):**
| Document Volume | Tokens/Month | Est. Cost/Month |
|-----------------|--------------|-----------------|
| Light (100 docs) | 500K | ~$0.07 |
| Medium (1,000 docs) | 5M | ~$0.65 |
| Heavy (10,000 docs) | 50M | ~$6.50 |

---

### 📊 Overall Resource Costs

| Resource | Pricing Basis | Low Estimate | High Estimate |
|----------|---------------|--------------|---------------|
| **Azure AI Foundry (GPT-5.2)** | Pay-per-use (tokens) - see breakdown above | $15 | $600 |
| **Document Intelligence (S0)** | Per 1,000 pages | $10 | $150 |
| **Azure AI Search (Standard)** | Fixed monthly | $250 | $250 |
| **Container App (epcon-ai)** | vCPU/Memory seconds | $35 | $50 |
| **Container App (worker)** | vCPU/Memory seconds | $35 | $50 |
| **Container Registry (Standard)** | Daily + storage | $20 | $20 |
| **Storage Account (RAGRS)** | GB stored + transactions | $5 | $20 |
| **PostgreSQL (B1ms, 32GB)** | Hourly + storage | $35 | $35 |
| **Key Vault (Standard)** | Operations + secrets | $1 | $5 |
| **Azure AD B2C/CIAM** | Per MAU (first 50K free) | $0 | $50 |

### Total Estimated Monthly Cost (2-5 Users)

| Category | Low Usage | Medium Usage | High Usage |
|----------|-----------|--------------|------------|
| AI Services (GPT-5.2) | $15 | $110 | $600 |
| AI Services (Doc Intel) | $10 | $50 | $150 |
| Azure AI Search | $250 | $250 | $250 |
| Compute (Container Apps) | $70 | $85 | $100 |
| Data (PostgreSQL, Storage) | $40 | $50 | $60 |
| Supporting Services | $25 | $40 | $75 |
| **TOTAL** | **~$410/month** | **~$585/month** | **~$1,235/month** |

**Per-User Cost Estimate:**
| Usage Level | 2 Users | 3 Users | 5 Users |
|-------------|---------|---------|---------|
| Low | ~$205/user | ~$137/user | ~$82/user |
| Medium | ~$293/user | ~$195/user | ~$117/user |
| High | ~$618/user | ~$412/user | ~$247/user |

---

## ⚠️ Key Cost Drivers

1. **Azure AI Search (Standard)** - Fixed ~$250/month regardless of usage
2. **Azure OpenAI (GPT-5.2)** - Variable based on token consumption ($1.75/M input, $14/M output)
3. **Container Apps Auto-scaling** - epcon-ai can scale to 10 replicas under load
4. **Embeddings (text-embedding-3-large)** - Costs increase with document ingestion volume

---

## 💡 Cost Optimization Recommendations

### Immediate Savings

1. **Downgrade Azure AI Search to Basic (~$75/month)**
   - Saves ~$175/month
   - Suitable if: <15 indexes, <3 replicas needed, query volume is moderate

2. **Set Azure OpenAI Spending Limits**
   - Configure budget alerts and caps in Azure Cost Management
   - Monitor token usage via Azure OpenAI metrics

3. **Review Container Apps Scaling**
   - Consider reducing max replicas from 10 to 3-5
   - Evaluate if worker needs to run 24/7

### Long-term Optimizations

4. **PostgreSQL Auto-pause**
   - Enable if database isn't needed continuously
   - Can reduce compute costs significantly for dev/test

5. **Storage Account Tier Review**
   - Consider downgrading from RAGRS to LRS if geo-redundancy not required
   - Move infrequently accessed data to Cool tier

6. **Reserved Capacity**
   - If usage is predictable, consider 1-year reserved capacity for PostgreSQL
   - Potential savings of 30-40%

---

## 🏢 Enterprise Deployment (Multi-Region, Zero-Downtime)

This section outlines the cost for a production-grade enterprise deployment with:
- ✅ Multi-region redundancy (East US 2 + West US 2)
- ✅ Zero-downtime deployments
- ✅ Zero data loss (RPO = 0, RTO < 15 min)
- ✅ High availability (99.99% SLA)
- ✅ Auto-failover capabilities

### 🌐 Architecture Overview

```
                    ┌─────────────────────┐
                    │   Azure Front Door  │
                    │   (Global LB + WAF) │
                    └──────────┬──────────┘
                               │
            ┌──────────────────┼──────────────────┐
            ▼                                     ▼
    ┌───────────────┐                     ┌───────────────┐
    │  East US 2    │                     │  West US 2    │
    │  (Primary)    │                     │  (Secondary)  │
    ├───────────────┤                     ├───────────────┤
    │ Container Apps│◄───────────────────►│ Container Apps│
    │ (2 replicas)  │    Traffic Manager  │ (2 replicas)  │
    ├───────────────┤                     ├───────────────┤
    │ PostgreSQL    │◄───geo-replication─►│ PostgreSQL    │
    │ (Primary)     │                     │ (Read Replica)│
    └───────────────┘                     └───────────────┘
            │                                     │
            └──────────────┬──────────────────────┘
                           ▼
                   ┌───────────────┐
                   │ Azure AI Search│
                   │ (3 replicas)   │
                   └───────────────┘
```

### 💰 Enterprise Infrastructure Costs

#### Compute & Networking

| Resource | Configuration | Region 1 | Region 2 | Monthly Cost |
|----------|---------------|----------|----------|--------------|
| **Azure Front Door** | Premium + WAF | Global | - | $335 |
| **Container Apps (epcon-ai)** | 1 vCPU, 2GB, min 2 replicas | $70 | $70 | $140 |
| **Container Apps (worker)** | 0.5 vCPU, 1GB, min 2 replicas | $70 | $70 | $140 |
| **Container Apps Environment** | Workload profiles | $50 | $50 | $100 |
| **Container Registry** | Premium (geo-replication) | Primary | Replicated | $150 |
| **Subtotal** | | | | **$865** |

#### Data & Storage

| Resource | Configuration | Region 1 | Region 2 | Monthly Cost |
|----------|---------------|----------|----------|--------------|
| **PostgreSQL Flexible** | General Purpose D2s_v3 (Primary) | 2 vCores, 128GB | - | $200 |
| **PostgreSQL Flexible** | General Purpose D2s_v3 (Read Replica) | - | 2 vCores, 128GB | $200 |
| **PostgreSQL Backup** | Geo-redundant backup | Enabled | - | $25 |
| **Storage Account** | GRS (Geo-redundant) | 100GB | Replicated | $25 |
| **Azure Files (Premium)** | For shared volumes | 100GB | 100GB | $30 |
| **Subtotal** | | | | **$480** |

#### AI Services

| Resource | Configuration | Monthly Cost |
|----------|---------------|--------------|
| **Azure AI Search** | Standard S2 (3 replicas for HA) | $750 |
| **AI Foundry (GPT-5.2)** | Pay-per-use ($1.75/M in, $14/M out) | $100 - $1,200 |
| **Document Intelligence** | S0 (pay-per-use) | $50 - $300 |
| **Content Safety** | Included in AI Services | $0 |
| **Subtotal** | | **$900 - $2,250** |

#### Security & Management

| Resource | Configuration | Monthly Cost |
|----------|---------------|--------------|
| **Key Vault** | Premium (HSM-backed) | $5 |
| **Azure Monitor** | Log Analytics (100GB/month) | $230 |
| **Application Insights** | Included with Monitor | $0 |
| **Azure AD B2C** | Premium P1 (1,000 MAU) | $50 |
| **Microsoft Defender** | For Cloud (optional) | $15/server |
| **Subtotal** | | **$300** |

#### Disaster Recovery Add-ons

| Resource | Configuration | Monthly Cost |
|----------|---------------|--------------|
| **Azure Site Recovery** | For VM-based workloads (if any) | $25/instance |
| **Traffic Manager** | DNS-based load balancing | $0.75/million queries |
| **Health Probes** | Endpoint monitoring | Included |
| **Subtotal** | | **~$50** |

---

### 📊 Enterprise Total Cost Summary

| Category | Low AI Usage | Medium AI Usage | High AI Usage |
|----------|--------------|-----------------|---------------|
| Compute & Networking | $865 | $865 | $865 |
| Data & Storage | $480 | $480 | $480 |
| AI Services (Search + GPT-5.2) | $900 | $1,250 | $2,250 |
| Security & Management | $300 | $300 | $300 |
| Disaster Recovery | $50 | $50 | $50 |
| **TOTAL** | **~$2,595/month** | **~$2,945/month** | **~$3,945/month** |

### 📈 Enterprise vs Current Comparison

| Aspect | Current Setup | Enterprise Setup | Difference |
|--------|---------------|------------------|------------|
| **Baseline Cost** | $381/month | $2,595/month | +$2,214 (+581%) |
| **Regions** | 1 (East US 2) | 2 (East US 2 + West US 2) | +1 region |
| **Availability SLA** | ~99.9% | 99.99% | +0.09% |
| **RPO (Data Loss)** | Up to 24 hours | ~0 (synchronous) | Near-zero |
| **RTO (Recovery Time)** | Hours | < 15 minutes | Automated |
| **PostgreSQL** | B1ms (Burstable) | D2s_v3 + Replica | 4x capacity |
| **AI Search** | Standard (1 replica) | S2 (3 replicas) | 3x redundancy |
| **Container Replicas** | 1-10 per region | 2-10 × 2 regions | 2x base capacity |

### 🎯 Enterprise SLA Guarantees

| Component | SLA | RPO | RTO |
|-----------|-----|-----|-----|
| Azure Front Door | 99.99% | N/A | Automatic |
| Container Apps | 99.95% | N/A | < 5 min (with 2+ replicas) |
| PostgreSQL (HA) | 99.99% | ~0 | < 30 sec |
| Azure AI Search (3 replicas) | 99.9% | N/A | Automatic |
| Storage (GRS) | 99.99999999999999% (16 9's) | ~0 | < 15 min |
| **Combined Effective SLA** | **~99.95%** | **~0** | **< 15 min** |

### 💡 Enterprise Cost Optimization Options

1. **Reserved Instances (1-year)**
   - PostgreSQL: Save 30-40% (~$160/month)
   - Container Apps: Volume discounts available

2. **Provisioned Throughput Units (PTU) for OpenAI**
   - If usage is predictable and high, PTU can reduce costs by 30-50%
   - Minimum commitment: ~$2,000/month for dedicated capacity

3. **Azure Hybrid Benefit**
   - If you have existing Windows Server licenses, apply to reduce VM costs

4. **Start with Active-Passive**
   - Deploy Region 2 as cold standby initially
   - Scale up to active-active as usage grows
   - Potential savings: ~$400/month

---

## 🌍 Enterprise Deployment: US + Europe (Cross-Continental)

This section compares using a **European region** (Sweden Central recommended) as the secondary region instead of West US 2, including service availability, latency, compliance, and cost implications.

### ✅ Recommended European Region: **Sweden Central**

Sweden Central is the best choice for a European secondary region because it has the most comprehensive AI service availability:

| Service | Sweden Central | West Europe | UK South | Notes |
|---------|----------------|-------------|----------|-------|
| **Azure OpenAI (gpt-5.1)** | ✅ | ❌ Limited | ✅ | Sweden has full model availability |
| **Azure OpenAI (gpt-4o)** | ✅ | ✅ | ✅ | All support gpt-4o |
| **text-embedding-3-large** | ✅ | ❌ | ✅ | Critical for your embeddings |
| **Azure AI Search** | ✅ | ✅ | ✅ | Full feature support |
| **Document Intelligence** | ✅ | ✅ | ✅ | S0 available |
| **Container Apps** | ✅ | ✅ | ✅ | Full support |
| **PostgreSQL Flexible** | ✅ | ✅ | ✅ | Geo-replica supported |
| **Availability Zones** | ✅ | ✅ | ✅ | HA supported |

### ⚠️ Key Considerations for US + Europe Setup

#### 1. **Latency Impact**
| Route | Latency | Impact |
|-------|---------|--------|
| East US 2 ↔ West US 2 | ~40-60ms | Minimal |
| East US 2 ↔ Sweden Central | ~90-120ms | Noticeable for sync replication |
| User (US) → Europe | ~80-100ms | May affect UX if routed to Europe |

#### 2. **Data Residency & Compliance**
| Consideration | US-US Setup | US-Europe Setup |
|---------------|-------------|-----------------|
| GDPR Compliance | ❌ Not required | ✅ May be required for EU users |
| Data Sovereignty | US only | Cross-border data transfer |
| SOC 2 / ISO 27001 | ✅ | ✅ (both regions compliant) |

#### 3. **Replication Limitations**
| Service | Cross-Region Replication | Notes |
|---------|--------------------------|-------|
| PostgreSQL | ✅ Geo-replica supported | Async only (RPO ~seconds, not zero) |
| Storage (GRS) | ✅ Supported | Async replication |
| AI Search | ⚠️ Manual sync required | No native cross-region replication |
| Container Registry | ✅ Geo-replication | Premium tier required |

### 💰 US + Europe Cost Comparison

#### Price Differences by Region

| Resource | East US 2 | Sweden Central | Difference |
|----------|-----------|----------------|------------|
| PostgreSQL D2s_v3 | $0.192/hour | $0.211/hour | **+10%** |
| Container Apps (vCPU) | $0.000012/sec | $0.000013/sec | **+8%** |
| Storage (GRS) | $0.061/GB | $0.067/GB | **+10%** |
| AI Search (Standard) | $250/month | $275/month | **+10%** |
| Data Transfer (Inter-region) | - | $0.02/GB | **New cost** |

#### US + Europe Enterprise Cost Breakdown

| Category | East US 2 (Primary) | Sweden Central (Secondary) | Monthly Total |
|----------|---------------------|----------------------------|---------------|
| **Compute & Networking** | | | |
| Azure Front Door (Premium + WAF) | Global | - | $335 |
| Container Apps (epcon-ai) | $70 | $76 | $146 |
| Container Apps (worker) | $70 | $76 | $146 |
| Container Apps Environment | $50 | $55 | $105 |
| Container Registry (Premium geo) | Primary | Replicated | $150 |
| **Subtotal** | | | **$882** |
| **Data & Storage** | | | |
| PostgreSQL D2s_v3 (Primary) | $200 | - | $200 |
| PostgreSQL D2s_v3 (Read Replica) | - | $220 | $220 |
| PostgreSQL Backup (Geo) | $25 | - | $25 |
| Storage Account (GRS) | 100GB | Replicated | $30 |
| Azure Files (Premium) | $15 | $17 | $32 |
| Inter-Region Data Transfer | - | ~50GB/month | $10 |
| **Subtotal** | | | **$517** |
| **AI Services** | | | |
| Azure AI Search (Standard S2) | $750 | - | $750 |
| AI Search Secondary (Manual sync)* | - | $275 (Standard S1) | $275 |
| AI Foundry (GPT-5.2) | Pay-per-use | - | $100-$1,200 |
| Document Intelligence | $50-$300 | - | $50-$300 |
| **Subtotal** | | | **$1,175 - $2,525** |
| **Security & Management** | | | |
| Key Vault (Premium) | $5 | $5 | $10 |
| Azure Monitor | $230 | $50 (basic) | $280 |
| Azure AD B2C | $50 | - | $50 |
| **Subtotal** | | | **$340** |

### 📊 US + Europe Total Cost Summary

| Category | Low AI Usage | Medium AI Usage | High AI Usage |
|----------|--------------|-----------------|---------------|
| Compute & Networking | $882 | $882 | $882 |
| Data & Storage | $517 | $517 | $517 |
| AI Services (GPT-5.2) | $1,175 | $1,525 | $2,525 |
| Security & Management | $340 | $340 | $340 |
| **TOTAL** | **~$2,914/month** | **~$3,264/month** | **~$4,264/month** |

### 📈 US-US vs US-Europe Comparison

| Aspect | US-US (East US 2 + West US 2) | US-Europe (East US 2 + Sweden Central) | Difference |
|--------|-------------------------------|----------------------------------------|------------|
| **Baseline Cost** | ~$2,595/month | ~$2,914/month | **+$319 (+12%)** |
| **With Medium AI** | ~$2,945/month | ~$3,264/month | **+$319 (+11%)** |
| **Latency (inter-region)** | 40-60ms | 90-120ms | +50-60ms |
| **RPO (PostgreSQL)** | ~0 (sync capable) | Seconds (async only) | Slightly higher |
| **GDPR Compliance** | ❌ | ✅ | Better for EU users |
| **Data Residency** | US only | US + EU | More complex |
| **Model Availability** | Full | Full (Sweden) | Equal |
| **AI Search Sync** | Manual | Manual | Same effort |

### 🎯 When to Choose US + Europe

| Choose US-Europe If... | Choose US-US If... |
|------------------------|-------------------|
| ✅ You have European users/customers | ✅ All users are in Americas |
| ✅ GDPR compliance is required | ✅ Lowest latency is priority |
| ✅ Data residency in EU is needed | ✅ Cost optimization is priority |
| ✅ True geographic diversity matters | ✅ Synchronous replication needed |
| ✅ Business continuity across continents | ✅ Simpler architecture preferred |

### ⚠️ Important Limitations for US + Europe

1. **Azure AI Search**: Does NOT support automatic cross-region replication
   - You'll need to maintain two separate indexes and sync them manually
   - Consider using a message queue (Service Bus) for index updates

2. **PostgreSQL Geo-Replica**: Async only for cross-continent
   - RPO is seconds, not zero
   - For true zero data loss, consider Azure Cosmos DB (global distribution)

3. **GPT-5.2 Model**: Check availability before deployment
   - East US 2: ✅ Available (Global Standard)
   - Sweden Central: ✅ Available (Global Standard)
   - West Europe: ❌ Limited availability
   - UK South: ✅ Available (alternative)

4. **Embeddings (text-embedding-3-large)**: 
   - Sweden Central: ✅ Available
   - Some European regions: ❌ Not available

---

## 🌐 Global Enterprise Deployment: 3-Region (East US + West US + Sweden Central)

This section outlines a truly global, maximum resilience deployment across **3 regions** spanning North America and Europe.

### 🏗️ Architecture Overview (3-Region)

```
                         ┌─────────────────────┐
                         │   Azure Front Door  │
                         │   (Global LB + WAF) │
                         └──────────┬──────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         ▼                          ▼                          ▼
┌─────────────────┐        ┌─────────────────┐        ┌─────────────────┐
│   East US 2     │        │   West US 2     │        │ Sweden Central  │
│   (Primary)     │        │  (US Secondary) │        │ (EU Secondary)  │
├─────────────────┤        ├─────────────────┤        ├─────────────────┤
│ Container Apps  │        │ Container Apps  │        │ Container Apps  │
│ (2 replicas)    │        │ (2 replicas)    │        │ (2 replicas)    │
├─────────────────┤        ├─────────────────┤        ├─────────────────┤
│ PostgreSQL      │◄──────►│ PostgreSQL      │        │ PostgreSQL      │
│ (Primary)       │  sync  │ (HA Replica)    │        │ (Async Replica) │
└────────┬────────┘        └────────┬────────┘        └────────┬────────┘
         │                          │                          │
         └──────────────────────────┼──────────────────────────┘
                                    ▼
                    ┌───────────────────────────────┐
                    │      Azure AI Search          │
                    │  Primary: East US 2 (S2, 3R)  │
                    │  Secondary: Sweden (S1)       │
                    └───────────────────────────────┘
```

### 💰 3-Region Infrastructure Costs

#### Compute & Networking

| Resource | East US 2 | West US 2 | Sweden Central | Monthly Total |
|----------|-----------|-----------|----------------|---------------|
| **Azure Front Door** | Global (Premium + WAF) | - | - | $335 |
| **Container Apps (epcon-ai)** | $70 | $70 | $76 | $216 |
| **Container Apps (worker)** | $70 | $70 | $76 | $216 |
| **Container Apps Environment** | $50 | $50 | $55 | $155 |
| **Container Registry** | Primary | Replicated | Replicated | $200 |
| **Subtotal** | | | | **$1,122** |

#### Data & Storage

| Resource | East US 2 | West US 2 | Sweden Central | Monthly Total |
|----------|-----------|-----------|----------------|---------------|
| **PostgreSQL D2s_v3 (Primary)** | $200 | - | - | $200 |
| **PostgreSQL D2s_v3 (US Replica)** | - | $200 | - | $200 |
| **PostgreSQL D2s_v3 (EU Replica)** | - | - | $220 | $220 |
| **PostgreSQL Backup (Geo)** | $25 | - | - | $25 |
| **Storage Account (GZRS)** | Primary | Replicated | Replicated | $45 |
| **Azure Files (Premium)** | $15 | $15 | $17 | $47 |
| **Inter-Region Data Transfer** | - | ~30GB | ~50GB | $20 |
| **Subtotal** | | | | **$757** |

#### AI Services

| Resource | Configuration | Monthly Cost |
|----------|---------------|--------------|
| **Azure AI Search (Primary)** | Standard S2 (3 replicas) - East US 2 | $750 |
| **Azure AI Search (Secondary)** | Standard S1 - Sweden Central | $275 |
| **AI Foundry (GPT-5.2)** | Pay-per-use ($1.75/M in, $14/M out) | $100 - $1,200 |
| **Document Intelligence** | S0 (East US 2 primary) | $50 - $300 |
| **Subtotal** | | **$1,175 - $2,525** |

#### Security & Management

| Resource | Configuration | Monthly Cost |
|----------|---------------|--------------|
| **Key Vault** | Premium × 2 (US + EU) | $10 |
| **Azure Monitor** | Log Analytics (150GB/month) | $345 |
| **Application Insights** | Included with Monitor | $0 |
| **Azure AD B2C** | Premium P1 (1,000 MAU) | $50 |
| **Azure Service Bus** | Standard (for AI Search sync) | $10 |
| **Subtotal** | | **$415** |

#### Disaster Recovery Add-ons

| Resource | Configuration | Monthly Cost |
|----------|---------------|--------------|
| **Traffic Manager** | DNS-based (3 endpoints) | $5 |
| **Health Probes** | 3 regions | Included |
| **Chaos Studio** | (Optional) DR testing | $20 |
| **Subtotal** | | **~$25** |

---

### 📊 3-Region Total Cost Summary

| Category | Low AI Usage | Medium AI Usage | High AI Usage |
|----------|--------------|-----------------|---------------|
| Compute & Networking | $1,122 | $1,122 | $1,122 |
| Data & Storage | $757 | $757 | $757 |
| AI Services (GPT-5.2) | $1,175 | $1,525 | $2,525 |
| Security & Management | $415 | $415 | $415 |
| Disaster Recovery | $25 | $25 | $25 |
| **TOTAL** | **~$3,494/month** | **~$3,844/month** | **~$4,844/month** |

---

### 📈 Cost Comparison: 2-Region vs 3-Region

| Setup | Low AI | Medium AI | High AI | vs 2-Region US-US |
|-------|--------|-----------|---------|-------------------|
| **Current (1 region)** | $410 | $585 | $1,235 | Baseline |
| **2-Region US-US** | $2,595 | $2,945 | $3,945 | - |
| **2-Region US-Europe** | $2,914 | $3,264 | $4,264 | +12% |
| **3-Region Global** | $3,494 | $3,844 | $4,844 | **+35%** |

### 🎯 3-Region vs 2-Region Feature Comparison

| Aspect | 2-Region (US-US) | 2-Region (US-EU) | 3-Region (US+US+EU) |
|--------|------------------|------------------|---------------------|
| **Monthly Cost (Medium)** | $2,945 | $3,264 | **$3,844** |
| **Additional Cost** | - | +$319 | **+$899** |
| **Geographic Coverage** | North America | NA + Europe | NA + Europe |
| **Failure Domains** | 2 | 2 | **3** |
| **Max Latency (users)** | ~60ms (US) | ~120ms (transatlantic) | **~60ms (local)** |
| **GDPR Compliance** | ❌ | ✅ | ✅ |
| **True Global HA** | ❌ | ⚠️ Partial | **✅ Full** |
| **PostgreSQL Replicas** | 1 | 1 | **2** |
| **Continent Failure Tolerance** | ❌ | ✅ | **✅** |

### 🌍 3-Region Benefits

1. **True Global Availability**
   - Users routed to nearest region automatically
   - US East Coast → East US 2
   - US West Coast → West US 2
   - Europe → Sweden Central

2. **Maximum Resilience**
   - Survives entire continent outage
   - 3 independent failure domains
   - No single point of failure

3. **Optimal User Experience**
   - < 60ms latency for all major markets
   - Local data processing for compliance

4. **Flexible Failover Options**
   - East US 2 fails → West US 2 + Sweden both active
   - Entire US region issue → Sweden handles all traffic
   - Sweden fails → Both US regions absorb load

### ⚡ 3-Region SLA Improvement

| Configuration | Effective SLA | Annual Downtime |
|---------------|---------------|-----------------|
| 1 Region | 99.9% | ~8.7 hours |
| 2 Regions | 99.99% | ~52 minutes |
| 3 Regions | **99.999%** | **~5 minutes** |

### 💡 3-Region Cost Optimization

1. **Active-Active-Passive Configuration**
   - Run East US 2 + West US 2 as active-active
   - Sweden Central as warm standby (scale down containers)
   - **Savings: ~$150/month**

2. **Shared AI Search**
   - Use single AI Search with global endpoint
   - Accept slightly higher latency for EU queries
   - **Savings: ~$275/month**

3. **Reserved Instances (1-year)**
   - PostgreSQL × 3 regions: Save ~$240/month
   - **Total 1-year savings: ~$2,880**

4. **Optimized 3-Region Configuration**

| Scenario | Monthly Cost | Notes |
|----------|--------------|-------|
| Full Active-Active-Active | $3,844 | Maximum resilience |
| Active-Active-Passive | $3,694 | EU on standby |
| Shared AI Search | $3,569 | Single search instance |
| **Fully Optimized** | **$3,394** | All optimizations |

---

### 📋 When to Choose 3-Region

| Choose 3-Region If... | Stick with 2-Region If... |
|-----------------------|---------------------------|
| ✅ Need "five 9s" (99.999%) uptime | ✅ 99.99% uptime is sufficient |
| ✅ Global user base (US + EU) | ✅ Users primarily in one continent |
| ✅ Regulatory requires multi-continent DR | ✅ Budget is constrained |
| ✅ Revenue loss > $50/min of downtime | ✅ Can tolerate ~1 hour/year downtime |
| ✅ Premium enterprise contracts | ✅ Standard SLA requirements |

---

## 🔄 Last Updated

- **Date:** December 16, 2025
- **Source:** Azure Resource Graph queries
- **Subscription:** Selected subscriptions in Azure account
