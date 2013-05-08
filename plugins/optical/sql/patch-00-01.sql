-- Initial schema for optical stores plugin.

CREATE TABLE optical_product (
    id bigserial NOT NULL PRIMARY KEY,
    te_id bigint UNIQUE REFERENCES transaction_entry(id),

    product_id bigint UNIQUE REFERENCES product(id) ON UPDATE CASCADE,

    optical_type integer,

    -- glass frames
    gf_glass_type text,
    gf_size text,
    gf_lens_type text,
    gf_color text,

    -- glass lenses
    gl_photosensitive text,
    gl_anti_glare text,
    gl_refraction_index text,
    gl_classification text,
    gl_addition text,
    gl_diameter text,
    gl_height text,
    gl_availability text,

    -- contact lenses
    cl_degree numeric(6,2),
    cl_classification text,
    cl_lens_type text,
    cl_discard text,
    cl_addition text,
    cl_cylindrical numeric(6, 2),
    cl_axis numeric(6, 2),
    cl_color text,
    cl_curvature text
);

CREATE TABLE optical_work_order (
    id bigserial NOT NULL PRIMARY KEY,
    te_id bigint UNIQUE REFERENCES transaction_entry(id),

    work_order_id bigint UNIQUE REFERENCES work_order(id) ON UPDATE CASCADE,
    prescription_date timestamp,
    patient text,

    -- TODO: Figure out if this space is enough
    od_esferico numeric(5, 2),

    oe_esferico numeric(5, 2)

);