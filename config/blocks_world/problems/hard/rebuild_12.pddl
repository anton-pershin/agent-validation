(define (problem rebuild-12)
  (:domain blocks-world)
  (:objects a b c d e f - block)
  (:init
    (clear a) (hand-empty) (on a c) (on b d) (on c e) (on d f) (on e b) (on-table f))
  (:goal (and (on a b) (on-table b) (on-table c) (on-table d) (on-table e) (on-table f)))
)
