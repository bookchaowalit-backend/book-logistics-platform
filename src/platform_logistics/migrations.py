from platform_backend_core import Migration

WORKSPACE = "workspace_id=nullif(current_setting('app.workspace_id',true),'')::uuid"
TABLES = ('shipment', 'shipment_label', 'tracking_event')

MIGRATIONS = [Migration('0001', """
CREATE TABLE shipment(
  workspace_id uuid NOT NULL, id uuid NOT NULL,
  idempotency_key text NOT NULL, fingerprint text NOT NULL,
  reference text NOT NULL CHECK(length(reference) BETWEEN 1 AND 64),
  recipient_ref text NOT NULL CHECK(length(recipient_ref) BETWEEN 1 AND 128),
  carrier text NOT NULL, service text NOT NULL,
  origin_country text NOT NULL, origin_postal_code text NOT NULL,
  destination_country text NOT NULL, destination_postal_code text NOT NULL,
  weight_grams integer NOT NULL CHECK(weight_grams BETWEEN 1 AND 30000),
  length_mm integer NOT NULL CHECK(length_mm BETWEEN 1 AND 1500),
  width_mm integer NOT NULL CHECK(width_mm BETWEEN 1 AND 1500),
  height_mm integer NOT NULL CHECK(height_mm BETWEEN 1 AND 1500),
  billable_weight_grams integer NOT NULL CHECK(billable_weight_grams > 0),
  rate_amount_minor bigint NOT NULL CHECK(rate_amount_minor >= 0), currency text NOT NULL,
  status text NOT NULL DEFAULT 'created' CHECK(status IN ('created','label_pending','label_uncertain','labelled','in_transit','out_for_delivery','exception','delivered','returned','cancelled')),
  last_event_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(workspace_id,id), UNIQUE(workspace_id,idempotency_key));
CREATE TABLE shipment_label(
  workspace_id uuid NOT NULL, shipment_id uuid NOT NULL, carrier text NOT NULL,
  status text NOT NULL CHECK(status IN ('purchasing','purchased','failed','uncertain')),
  attempts integer NOT NULL DEFAULT 1 CHECK(attempts >= 1),
  lease_token uuid, lease_until timestamptz, tracking_number text, label_ref text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(workspace_id,shipment_id),
  FOREIGN KEY(workspace_id,shipment_id) REFERENCES shipment(workspace_id,id),
  UNIQUE(workspace_id,carrier,tracking_number),
  CHECK((status='purchased')=(tracking_number IS NOT NULL AND label_ref IS NOT NULL)));
CREATE TABLE tracking_event(
  workspace_id uuid NOT NULL, carrier text NOT NULL, carrier_event_id text NOT NULL,
  shipment_id uuid NOT NULL, status text NOT NULL, occurred_at timestamptz NOT NULL,
  fingerprint text NOT NULL, outcome text NOT NULL CHECK(outcome IN ('applied','recorded')),
  received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(workspace_id,carrier,carrier_event_id),
  FOREIGN KEY(workspace_id,shipment_id) REFERENCES shipment(workspace_id,id));
CREATE INDEX ON tracking_event(workspace_id,shipment_id,occurred_at);
""" + ''.join(f"""
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY workspace ON {t} USING({WORKSPACE}) WITH CHECK({WORKSPACE});
""" for t in TABLES))]
